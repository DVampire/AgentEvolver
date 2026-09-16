"""The held history of one run.

This is the source of truth, not a projection of one. The previous design rebuilt the
message list every step from a persisted trace, which made durable logging a correctness
dependency: nothing could be sent to the model unless the log could be read back and its
fold surface still agreed with itself. Here the list *is* the state, and the trace is
written from it — so a broken writer costs observability, never the run.

A conversation holds four things because the request has four layers, and three of them
map straight onto stored state:

    system      + task    → the fixed layer, written once
    checkpoint            → the folded summary, when history has been compacted
    items                 → the exact assistant/tool turns still replayed

The fourth, ``live``, is assembled per step and never stored: it is this step's volatile
state, and storing it would be storing something already stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agentevolver.message.types import (
    AssistantMessage,
    CompactionMessage,
    HumanMessage,
    Message,
    SystemMessage,
    ToolMessage,
)


class Conversation:
    """One run's messages, appended in order."""

    def __init__(
        self,
        *,
        system: Optional[Sequence[Message]] = None,
        task: str = "",
    ) -> None:
        #: Instructions that do not change during the run.
        self.system: List[Message] = list(system or ())
        #: What this run was asked to do. Rendered into the fixed layer as one anchor.
        self.task: str = task
        #: The single canonical compaction summary, once history has been folded.
        self.checkpoint: Optional[CompactionMessage] = None
        #: Exact turns: assistant messages and the tool results that answer them.
        self.items: List[Message] = []
        self._event_ids: set[str] = set()
        # References to ordinary history messages, for restoring current state on fold.
        self._observations: Dict[str, HumanMessage] = {}

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def append(self, message: Message) -> None:
        self.items.append(message)

    def extend(self, messages: Iterable[Message]) -> None:
        self.items.extend(messages)

    def add_turn(
        self, assistant: AssistantMessage, results: Sequence[ToolMessage] = ()
    ) -> None:
        """Record one complete step: the model's turn and every result answering it.

        Written as a unit deliberately. A turn is only valid when all of its tool
        results are present, so a partially recorded one is never a state this object
        can be left in by a normal write.
        """
        self.items.append(assistant)
        self.items.extend(results)

    def note(self, text: str, *, event_id: str = "") -> None:
        """Append a runtime note as a user turn — a nudge, a delivered event."""
        if text.strip() and (not event_id or event_id not in self._event_ids):
            if not self.complete:
                raise ValueError("Runtime notes require a closed tool turn")
            self.items.append(HumanMessage(content=text))
            if event_id:
                self._event_ids.add(event_id)

    def observe(self, key: str, text: str) -> None:
        """Append a changed state once; retain its full latest value across folds."""
        previous = self._observations.get(key)
        if previous is not None and previous.text == text:
            return
        if not self.complete:
            raise ValueError("Context observations require a closed tool turn")
        if not text.strip():
            if previous is not None:
                self.note(f"Context {key!r} is no longer active.")
                self._observations.pop(key)
            return
        message = HumanMessage(content=text)
        self.items.append(message)
        self._observations[key] = message

    @property
    def observations(self) -> Sequence[HumanMessage]:
        """Current state retained verbatim across folds, already present in history."""
        return tuple(self._observations.values())

    def set_system(self, messages: Sequence[Message]) -> None:
        """Replace the fixed instructions. Called once, before the first step."""
        self.system = [
            message if isinstance(message, Message) else SystemMessage(content=str(message))
            for message in messages
        ]

    def save(self, path: Path, *, model: str, agent: str) -> None:
        """Persist a closed boundary, including opaque provider items, without an LLM."""
        from agentevolver.utils.file_utils import atomic_write_text

        if not self.complete:
            raise ValueError("Cannot save a conversation with unanswered tool calls")
        document = {
            "version": 1, "model": model, "agent": agent, "task": self.task,
            "system": [m.model_dump(mode="json") for m in self.system],
            "checkpoint": self.checkpoint.model_dump(mode="json") if self.checkpoint else None,
            "items": [m.model_dump(mode="json") for m in self.items],
            "event_ids": sorted(self._event_ids),
            "observations": {
                key: next(i for i, item in enumerate(self.items) if item is message)
                for key, message in self._observations.items()
            },
        }
        atomic_write_text(path, json.dumps(document, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path, *, model: str, agent: str) -> "Conversation":
        """Explicit same-route resume. Never silently replay state on another model."""
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("version") != 1 or document.get("agent") != agent:
            raise ValueError("Conversation schema or agent identity does not match")
        if document.get("model") != model:
            raise ValueError("Resume requires the original model route; native state is not portable")
        classes = {"user": HumanMessage, "system": SystemMessage,
                   "assistant": AssistantMessage, "tool": ToolMessage}
        def parse(value: Dict[str, Any]) -> Message:
            return classes[value["role"]].model_validate(value)
        result = cls(system=[parse(m) for m in document["system"]], task=document["task"])
        if document.get("checkpoint") is not None:
            checkpoint = dict(document["checkpoint"])
            # Conversation snapshots predate the explicit scope field, but their
            # checkpoints have always been made from foldable history, not fixed.
            checkpoint.setdefault("compaction_scope", "history")
            result.checkpoint = CompactionMessage.model_validate(checkpoint)
        result.items = [parse(m) for m in document["items"]]
        result._event_ids = set(document.get("event_ids") or ())
        for key, index in (document.get("observations") or {}).items():
            if (type(index) is not int or not 0 <= index < len(result.items)
                    or not isinstance(result.items[index], HumanMessage)):
                raise ValueError("Invalid saved context observation")
            result._observations[key] = result.items[index]
        result._remove_legacy_plan_snapshots()
        if not result.complete:
            raise ValueError("Saved conversation has unanswered tool calls")
        from agentevolver.agent.context.envelope import ContextEnvelope
        ContextEnvelope(
            fixed=tuple(result.system), recent=tuple(result.items),
            checkpoint=(result.checkpoint,) if result.checkpoint is not None else (),
        ).validate()
        return result

    def _remove_legacy_plan_snapshots(self) -> None:
        """Migrate old automatic plan projections when explicitly resuming a thread.

        These tagged runtime snapshots used to be user turns, so Responses retained
        every revision inside its native checkpoint. The plan file now supplies live
        state. Remove only standalone projections; real user feedback, tool reads,
        readable historical summaries and opaque compaction items remain untouched.
        Saved source files/archives are not modified by loading.
        """
        def is_projection(text: str) -> bool:
            return text.strip().startswith('<plan-context ') and text.strip().endswith('</plan-context>')

        removed = {id(m) for m in self.items if isinstance(m, HumanMessage) and is_projection(m.text)}
        kept_messages = []
        for message in self.items:
            if id(message) in removed:
                continue
            if (removed and kept_messages and isinstance(message, AssistantMessage)
                    and isinstance(kept_messages[-1], AssistantMessage)):
                kept_messages.append(HumanMessage(
                    content="[Automatic plan snapshot omitted; read the current live plan Brief.]"
                ))
            kept_messages.append(message)
        self.items = kept_messages
        self._observations = {key: m for key, m in self._observations.items() if id(m) not in removed}
        if self.checkpoint is None:
            return
        state = self.checkpoint.provider_state or {}
        responses = state.get("responses") or {}
        native = responses.get("compaction_items") or []

        def is_native_projection(item: Dict[str, Any]) -> bool:
            if item.get("type") != "message" or item.get("role") != "user":
                return False
            content = item.get("content")
            if not isinstance(content, list) or not content:
                return False
            if not all(isinstance(part, dict) and part.get("type") == "input_text"
                       and isinstance(part.get("text"), str) for part in content):
                return False
            return is_projection("\n".join(part["text"] for part in content))

        kept = [item for item in native if not is_native_projection(item)]
        if len(kept) != len(native):
            self.checkpoint.provider_state = {
                **state, "responses": {**responses, "compaction_items": kept},
            }

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def reference(self, max_chars: int) -> str:
        """Portable fork evidence, bounded by whole assistant/tool cycles.

        This is reference data, not native replay under a different system prompt.
        Opaque protocol state stays in the original conversation/snapshot. Content,
        tool arguments and results are preserved without slicing any string.
        """
        groups, current, pending = [], [], set()
        has_assistant = False
        for message in self.items:
            if isinstance(message, (AssistantMessage, HumanMessage)) and has_assistant:
                if pending:
                    break
                groups.append(current)
                current, has_assistant = [], False
            if isinstance(message, AssistantMessage):
                has_assistant = True
                pending = {call.id for call in message.tool_calls}
            elif isinstance(message, ToolMessage):
                if message.tool_call_id not in pending:
                    break
                pending.remove(message.tool_call_id)
            current.append(message)
        if current and not pending:
            groups.append(current)

        def encode(group):
            return [message.model_dump(mode="json", exclude={"provider_state", "cache"},
                                       exclude_none=True) for message in group]

        kept, size = [], 0
        for group in reversed(groups):
            value = encode(group)
            cost = len(json.dumps(value, ensure_ascii=False))
            if kept and size + cost > max_chars:
                break
            kept.append(value)
            size += cost
        omitted = sum(len(group) for group in groups[:len(groups) - len(kept)])
        document = {
            "task": self.task,
            "checkpoint": self.checkpoint.text if self.checkpoint is not None else "",
            "turns": list(reversed(kept)),
            "omitted_messages": omitted,
            "incomplete_turn_excluded": bool(pending),
        }
        notice = f"[{omitted} earlier message(s) omitted as whole turns]\n" if omitted else ""
        return notice + json.dumps(document, ensure_ascii=False)

    @property
    def turns(self) -> int:
        """How many assistant turns are still held exactly."""
        return sum(1 for message in self.items if isinstance(message, AssistantMessage))

    def turn_starts(self) -> List[int]:
        """Indices of each held assistant turn, oldest first."""
        return [
            index for index, message in enumerate(self.items)
            if isinstance(message, AssistantMessage)
        ]

    def tail(self, turns: int) -> List[Message]:
        """The last ``turns`` complete turns, starting at an assistant message."""
        starts = self.turn_starts()
        if turns <= 0 or not starts:
            return []
        if len(starts) <= turns:
            return list(self.items)
        return self.items[starts[-turns]:]

    def pending_tool_calls(self) -> List[str]:
        """Tool-call ids emitted but not yet answered.

        Non-empty means the conversation is mid-turn and must not be sent, compacted or
        checkpointed. Callers use it as the "is this a safe boundary" test.
        """
        pending: List[str] = []
        for message in self.items:
            if isinstance(message, AssistantMessage):
                pending.extend(str(call.id) for call in message.tool_calls)
            elif isinstance(message, ToolMessage):
                call_id = str(message.tool_call_id)
                if call_id in pending:
                    pending.remove(call_id)
        return pending

    @property
    def complete(self) -> bool:
        """Whether every emitted tool call has its result."""
        return not self.pending_tool_calls()

    # ------------------------------------------------------------------
    # Folding
    # ------------------------------------------------------------------

    def foldable(self, keep_turns: int) -> List[Message]:
        """The messages a fold would remove, given how many turns to retain.

        Empty when there is nothing safe to fold. Callers use it to decide whether to
        spend a model call summarising, and to hand the provider exactly the history it
        is being asked to compact.
        """
        starts = self.turn_starts()
        if len(starts) <= keep_turns:
            return []
        cut = starts[-keep_turns] if keep_turns > 0 else len(self.items)
        # A delivered user request belongs with the assistant that answers it, not
        # with the older turn being removed. Keep that boundary intact after a wake.
        if keep_turns > 0:
            while cut > 0 and isinstance(self.items[cut - 1], HumanMessage):
                cut -= 1
        return list(self.items[:cut])

    def fold(
        self,
        summary: str,
        keep_turns: int,
        *,
        provider_state: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Replace everything before the last ``keep_turns`` turns with one checkpoint.

        The cut always lands on an assistant message, so the kept tail never opens with
        an orphan tool result. Returns how many messages were folded away; zero when
        there was nothing safe to fold.

        ``provider_state`` carries a native checkpoint — an opaque Responses compaction
        item, or an Anthropic ``compact_20260112`` block. It travels on the message
        because that is what the provider serializer replays and what the assembler reads
        to decide where the cache prefix now ends. The text summary stays alongside it as
        the readable companion; for a route with no native compaction it is the whole
        checkpoint.

        Never rewrites what was already said — the checkpoint supersedes it. That is also
        what the cache wants: the prefix behind the fold point stops changing.
        """
        folded = self.foldable(keep_turns)
        if not folded:
            return 0
        # The compactor already receives the prior checkpoint. Its output supersedes
        # it; appending the old summary again grows memory on every fold.
        body = summary.strip()
        self.checkpoint = CompactionMessage(
            content=f"<memory-checkpoint>\n{body}\n</memory-checkpoint>",
            provider_state=dict(provider_state or {}),
            compaction_scope="history",
        )
        self.items = self.items[len(folded):]
        # A summary may condense historical plans; current state remains exact.
        retained = {id(message) for message in self.items}
        self.items.extend(message for message in self._observations.values()
                          if id(message) not in retained)
        return len(folded)

    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.items)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        folded = "folded" if self.checkpoint else "whole"
        return f"Conversation(turns={self.turns}, items={len(self.items)}, {folded})"


__all__ = ["Conversation"]
