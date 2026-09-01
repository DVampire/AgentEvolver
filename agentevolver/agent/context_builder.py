"""Build the model-facing conversation from prompt scaffolding and Trace.

The prompt renderer owns instructions and live state. Trace owns what happened. This
module is the only place that combines those two sources into a request history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Tuple

from agentevolver.message.types import (
    AssistantMessage,
    CompactionMessage,
    HumanMessage,
    Message,
    ToolMessage,
)
from agentevolver.trace.derive import _marker, derive_messages
from agentevolver.trace.surface import fold_surface
from agentevolver.trace.types import TraceEventType


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_DATA_BLOCKS = ("task", "inherited-context", "memory", "working-memory", "recent-steps")
_LAYERS = ("fixed", "checkpoint", "recent", "live")


class ContextProtocolError(ValueError):
    """The model-facing context would violate the four-layer protocol."""


class ContextMessages(list[Message]):
    """A normal message list that retains its validated layer accounting."""

    def __init__(self, messages: Iterable[Message], layer_tokens: dict[str, int]):
        super().__init__(messages)
        self.layer_tokens = dict(layer_tokens)


@dataclass(frozen=True)
class ContextEnvelope:
    """One validated fixed → checkpoint → recent → live model context.

    The envelope is provider-neutral. It is flattened only after its invariants hold,
    so serializers cannot accidentally reorder a checkpoint, duplicate a context tier,
    or receive a severed assistant/tool turn.
    """

    fixed: tuple[Message, ...] = field(default_factory=tuple)
    checkpoint: tuple[CompactionMessage, ...] = field(default_factory=tuple)
    recent: tuple[Message, ...] = field(default_factory=tuple)
    live: tuple[Message, ...] = field(default_factory=tuple)

    def validate(self) -> "ContextEnvelope":
        if len(self.checkpoint) > 1:
            raise ContextProtocolError("context may contain only one canonical checkpoint")
        if any(not isinstance(message, CompactionMessage) for message in self.checkpoint):
            raise ContextProtocolError("checkpoint layer accepts only CompactionMessage")
        other_layers = (*self.fixed, *self.recent, *self.live)
        if any(isinstance(message, CompactionMessage) for message in other_layers):
            raise ContextProtocolError("CompactionMessage must live only in checkpoint")
        if any(message.role not in {"system", "user"} for message in self.fixed):
            raise ContextProtocolError("fixed layer accepts only system/task messages")
        if any(message.role != "user" for message in self.live):
            raise ContextProtocolError("live layer accepts only current user context")
        if any(message.role == "system" for message in self.recent):
            raise ContextProtocolError("system messages must remain in the fixed layer")

        seen_user = False
        for message in self.fixed:
            if message.role == "user":
                seen_user = True
            elif seen_user:
                raise ContextProtocolError("fixed system messages must precede the task anchor")

        all_messages = (*self.fixed, *self.checkpoint, *self.recent, *self.live)
        if len({id(message) for message in all_messages}) != len(all_messages):
            raise ContextProtocolError("one message cannot belong to multiple context layers")
        self._validate_tool_turns()
        return self

    def _validate_tool_turns(self) -> None:
        pending: set[str] = set()
        seen: set[str] = set()
        previous_assistant = False
        for message in self.recent:
            if isinstance(message, AssistantMessage):
                if pending:
                    raise ContextProtocolError(
                        f"assistant turn started before tool results arrived: {sorted(pending)}"
                    )
                if previous_assistant:
                    raise ContextProtocolError(
                        "adjacent assistant turns cannot preserve provider-owned reasoning state"
                    )
                ids = [str(call.id) for call in message.tool_calls]
                if len(ids) != len(set(ids)) or any(call_id in seen for call_id in ids):
                    raise ContextProtocolError("tool-call ids must be unique in the exact tail")
                pending.update(ids)
                seen.update(ids)
                previous_assistant = True
            elif isinstance(message, ToolMessage):
                call_id = str(message.tool_call_id)
                if call_id not in pending:
                    raise ContextProtocolError(f"orphan tool result: {call_id}")
                pending.remove(call_id)
                previous_assistant = False
            elif pending:
                raise ContextProtocolError(
                    f"tool results must immediately follow their assistant turn: {sorted(pending)}"
                )
            else:
                previous_assistant = False
        if pending:
            raise ContextProtocolError(f"tool turn is incomplete: {sorted(pending)}")

    def token_counts(self) -> dict[str, int]:
        from agentevolver.model.pressure import estimate_tokens

        return {
            layer: estimate_tokens(list(getattr(self, layer)))
            if getattr(self, layer) else 0
            for layer in _LAYERS
        }

    def flatten(self) -> ContextMessages:
        self.validate()
        messages: list[Message] = []
        for layer in _LAYERS:
            messages.extend(
                message.model_copy(update={"context_layer": layer})
                for message in getattr(self, layer)
            )
        return ContextMessages(messages, self.token_counts())


def _strip_template_comments(message: Message) -> Message:
    """Remove author-only HTML comments from one rendered prompt message.

    Trace-derived task, assistant and tool content never passes through here: comments in
    repository files or tool output are user data and must remain exact. Only prompt
    scaffolding rendered from our HTML templates is cleaned.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str) or "<!--" not in content:
        return message
    if getattr(message, "role", "") == "system":
        return message.model_copy(update={"content": _HTML_COMMENT.sub("", content)})

    protected: list[str] = []

    def hold(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00AGENTEVOLVER_DATA_{len(protected) - 1}\x00"

    names = "|".join(re.escape(name) for name in _DATA_BLOCKS)
    cleaned = re.sub(rf"<({names})>.*?</\1>", hold, content, flags=re.S)
    cleaned = _HTML_COMMENT.sub("", cleaned)
    for index, value in enumerate(protected):
        cleaned = cleaned.replace(f"\x00AGENTEVOLVER_DATA_{index}\x00", value)
    return message.model_copy(update={"content": cleaned})


def strip_rendered_comments(rendered: List[Message]) -> List[Message]:
    """Strip template documentation while preserving task and memory payloads."""
    cleaned = [_strip_template_comments(message) for message in rendered]
    return rendered if all(a is b for a, b in zip(cleaned, rendered)) else cleaned


class ContextBuilder:
    """Create a cache-friendly task/checkpoint/conversation/live-state layout."""

    def build(self, rendered: List[Message], events: List[Any], ctx: Any) -> List[Message]:
        return self.build_envelope(rendered, events, ctx).flatten()

    def build_envelope(
        self, rendered: List[Message], events: List[Any], ctx: Any
    ) -> ContextEnvelope:
        system = [
            _strip_template_comments(m)
            for m in rendered if getattr(m, "role", "") == "system"
        ]
        anchor, live = self.split_rendered_turn(rendered)
        derived = derive_messages(events)
        if not derived:
            if anchor:
                anchor[-1].cache = True
            return ContextEnvelope(
                fixed=tuple(system + anchor), live=tuple(live)
            ).validate()

        task = self._task(events)
        checkpoints = self._checkpoints(events)
        checkpoint_texts = [str(event.message or "") for event in checkpoints]
        # Tool execution already owns the large-output policy: full output is spilled
        # and the result carries its durable locator. Slicing it again here silently
        # changed the canonical conversation and could even remove that locator.
        recent = self._recent_messages(derived, task, checkpoint_texts)

        if not anchor:
            anchor = [HumanMessage(content=f"<task>\n{task}\n</task>")]
        elif task:
            # Trace owns the original task. The renderer usually contains the same
            # bytes, but a stale/derived prompt must not silently redefine the anchor.
            text = anchor[-1].text
            task_block = f"<task>\n{task}\n</task>"
            if re.search(r"<task>.*?</task>", text, re.S):
                text = re.sub(r"<task>.*?</task>", task_block, text, count=1, flags=re.S)
            else:
                text = f"{task_block}\n{text}"
            anchor[-1] = anchor[-1].model_copy(update={"content": text})
        anchor[-1].cache = True
        checkpoint = []
        if checkpoints:
            event = checkpoints[-1]
            checkpoint_message = CompactionMessage(
                content=(
                    f"<memory-checkpoint>\n{event.message or ''}\n"
                    "</memory-checkpoint>"
                ),
                provider_state=getattr(event, "provider_state", None) or {},
            )
            # Claude ignores everything before a native compaction block. Cache that
            # block, not the now-inactive task copy; the summary itself carries the task.
            anthropic = (checkpoint_message.provider_state or {}).get("anthropic") or {}
            if anthropic.get("compaction_blocks"):
                anchor[-1].cache = False
                checkpoint_message.cache = True
            checkpoint.append(checkpoint_message)

        frozen = checkpoint + recent
        if frozen:
            # The Opus relay cache probe verified assistant boundaries (including an
            # empty-text tool-call turn) but not tool-role boundaries. Cache through
            # the newest assistant and leave its results in the live suffix.
            boundary = next(
                (message for message in reversed(frozen)
                 if isinstance(message, AssistantMessage)),
                frozen[-1],
            )
            boundary.cache = True
        return ContextEnvelope(
            fixed=tuple(system + anchor),
            checkpoint=tuple(checkpoint),
            recent=tuple(recent),
            live=tuple(live),
        ).validate()

    @staticmethod
    def _task(events: List[Any]) -> str:
        for event in events:
            if event.event_type == TraceEventType.AGENT_START:
                return str((event.input or {}).get("task") or "")
        return ""

    @staticmethod
    def _checkpoints(events: List[Any]) -> List[Any]:
        surface = set(fold_surface(events)["nodes"])
        return [
            event
            for event in events
            if event.seq_no in surface
            and event.event_type == TraceEventType.CUSTOM
            and _marker(event) == "compaction"
            and event.message
        ]

    @staticmethod
    def _recent_messages(
        messages: List[Message], task: str, checkpoints: List[str]
    ) -> List[Message]:
        """Remove duplicate context turns and non-replayable empty assistant seams."""
        checkpoint_set = set(checkpoints)
        recent: List[Message] = []
        task_removed = False
        for message in messages:
            if isinstance(message, CompactionMessage):
                # Reinstalled once below from its Trace event, where the opaque native
                # payload is available alongside the readable fallback checkpoint.
                continue
            if getattr(message, "role", "") == "user":
                text = getattr(message, "text", "") or ""
                if not task_removed and text == task:
                    task_removed = True
                    continue
                if text in checkpoint_set:
                    continue
            if (
                isinstance(message, AssistantMessage)
                and recent
                and isinstance(recent[-1], AssistantMessage)
            ):
                previous = recent[-1]
                previous_empty = not previous.text and not previous.tool_calls
                current_empty = not message.text and not message.tool_calls
                # A no-action response may contain provider-owned reasoning state for
                # audit. It is replayable while it is the latest assistant response,
                # but not beside another assistant turn: Anthropic combines adjacent
                # roles and then rejects the signed blocks as modified. Preserve the
                # actionable/newer turn; keep standalone persisted reasoning intact.
                if previous_empty:
                    recent.pop()
                elif current_empty:
                    continue
            recent.append(message)
        return recent

    @staticmethod
    def split_rendered_turn(rendered: List[Message]) -> Tuple[List[Message], List[Message]]:
        """Extract the immutable task/inherited context and the volatile live tail."""
        turn = next(
            (m for m in reversed(rendered) if getattr(m, "role", "") != "system"), None
        )
        if turn is None:
            return [], []
        text = getattr(turn, "text", "") or ""

        anchor_parts: List[str] = []
        for block in ("task", "inherited-context"):
            match = re.search(rf"<{block}>.*?</{block}>", text, re.S)
            if match:
                anchor_parts.append(match.group(0))
            text = re.sub(rf"<{block}>.*?</{block}>", "", text, flags=re.S)

        # All callable capabilities already travel in the provider-native `tools`
        # parameter. Repeating their descriptions in a user message costs tokens and
        # gives the model two catalogs that can drift.
        text = re.sub(
            r"<capability-context>.*?</capability-context>", "", text, flags=re.S
        )
        for block in (
            "tool-context", "skill-context", "connector-context", "workflow-context",
            "plugin-context", "subagent-context",
        ):
            text = re.sub(rf"<{block}>.*?</{block}>", "", text, flags=re.S)

        # Trace/checkpoints are the history. Keeping the rendered memory tiers here
        # would state the same actions again as prose in the volatile tail.
        for block in ("memory", "working-memory", "recent-steps"):
            text = re.sub(rf"<{block}>.*?</{block}>", "", text, flags=re.S)

        # Module comments document the HTML templates for maintainers; they are not
        # instructions. Strip them only after extracting task/inherited-context so an
        # HTML comment supplied by the benchmark remains byte-exact in the anchor.
        text = _HTML_COMMENT.sub("", text)

        def turn(body: str) -> List[Message]:
            body = body.strip()
            return [HumanMessage(content=body)] if re.sub(r"<[^>]+>", "", body).strip() else []

        return turn("\n".join(anchor_parts)), turn(text)

context_builder = ContextBuilder()

__all__ = [
    "ContextBuilder",
    "ContextEnvelope",
    "ContextMessages",
    "ContextProtocolError",
    "context_builder",
    "strip_rendered_comments",
]
