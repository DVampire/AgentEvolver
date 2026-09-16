"""ContextBuilder: turn a run's history into the four-layer request.

This is the only place that decides what a model sees and in what order. Agents ask for
a context; they do not assemble one. Keeping that boundary is what stops prompt layout
from being re-implemented slightly differently in each actor.

This builder projects a persisted trace. The held-conversation builder used by the
process-kernel agents is :mod:`agentevolver.agent.context.assembler`; both produce the
same :class:`~agentevolver.agent.context.envelope.ContextEnvelope`, so everything
downstream - cache breakpoints, pressure accounting, provider serialisation - is shared.
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from agentevolver.agent.context.envelope import ContextEnvelope
from agentevolver.agent.context.sanitize import _HTML_COMMENT, _strip_template_comments
from agentevolver.message.types import (
    AssistantMessage,
    CompactionMessage,
    HumanMessage,
    Message,
)
from agentevolver.trace.derive import _marker, derive_messages
from agentevolver.trace.surface import fold_surface
from agentevolver.trace.types import TraceEventType


class ContextBuilder:
    """Build a cache-friendly task/checkpoint/conversation/live-state request."""

    def build(self, rendered: List[Message], events: List[Any], ctx: Any) -> List[Message]:
        return self.build_envelope(rendered, events, ctx).flatten()

    def build_envelope(
        self, rendered: List[Message], events: List[Any], ctx: Any,
    ) -> ContextEnvelope:
        system = [
            _strip_template_comments(message)
            for message in rendered if getattr(message, "role", "") == "system"
        ]
        anchor, live = self.split_rendered_turn(rendered)
        derived = derive_messages(events)
        if not derived:
            if anchor:
                anchor[-1].cache = True
            return ContextEnvelope(fixed=tuple(system + anchor), live=tuple(live)).validate()

        task = self._task(events)
        checkpoints = self._checkpoints(events)
        recent = self._recent_messages(
            derived, task, [str(event.message or "") for event in checkpoints],
        )
        if not anchor:
            anchor = [HumanMessage(content=f"<task>\n{task}\n</task>")]
        elif task:
            text = anchor[-1].text
            task_block = f"<task>\n{task}\n</task>"
            if re.search(r"<task>.*?</task>", text, re.S):
                text = re.sub(r"<task>.*?</task>", task_block, text, count=1, flags=re.S)
            else:
                text = f"{task_block}\n{text}"
            anchor[-1] = anchor[-1].model_copy(update={"content": text})
        anchor[-1].cache = True

        checkpoint: List[CompactionMessage] = []
        if checkpoints:
            event = checkpoints[-1]
            checkpoint_message = CompactionMessage(
                content=f"<memory-checkpoint>\n{event.message or ''}\n</memory-checkpoint>",
                provider_state=getattr(event, "provider_state", None) or {},
            )
            anthropic = (checkpoint_message.provider_state or {}).get("anthropic") or {}
            if anthropic.get("compaction_blocks"):
                anchor[-1].cache = False
                checkpoint_message.cache = True
            checkpoint.append(checkpoint_message)

        frozen: List[Message] = [*checkpoint, *recent]
        if frozen:
            boundary = next(
                (message for message in reversed(frozen)
                 if isinstance(message, AssistantMessage)),
                frozen[-1],
            )
            boundary.cache = True
        return ContextEnvelope(
            fixed=tuple(system + anchor), checkpoint=tuple(checkpoint),
            recent=tuple(recent), live=tuple(live),
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
            event for event in events
            if event.seq_no in surface
            and event.event_type == TraceEventType.CUSTOM
            and _marker(event) == "compaction"
            and event.message
        ]

    @staticmethod
    def _recent_messages(
        messages: List[Message], task: str, checkpoints: List[str],
    ) -> List[Message]:
        checkpoint_set = set(checkpoints)
        recent: List[Message] = []
        task_removed = False
        for message in messages:
            if isinstance(message, CompactionMessage):
                continue
            if getattr(message, "role", "") == "user":
                text = getattr(message, "text", "") or ""
                if not task_removed and text == task:
                    task_removed = True
                    continue
                if text in checkpoint_set:
                    continue
            if isinstance(message, AssistantMessage) and recent and isinstance(
                recent[-1], AssistantMessage,
            ):
                previous = recent[-1]
                previous_empty = not previous.text and not previous.tool_calls
                current_empty = not message.text and not message.tool_calls
                if previous_empty:
                    recent.pop()
                elif current_empty:
                    continue
                else:
                    recent.append(HumanMessage(content=(
                        "<runtime-followup>Continue with the next tool action, or finish "
                        "explicitly if the task is complete.</runtime-followup>"
                    )))
            recent.append(message)
        return recent

    @staticmethod
    def split_rendered_turn(rendered: List[Message]) -> Tuple[List[Message], List[Message]]:
        """Extract immutable task/inherited context and the volatile live tail."""
        turn_message = next(
            (message for message in reversed(rendered)
             if getattr(message, "role", "") != "system"),
            None,
        )
        if turn_message is None:
            return [], []
        text = getattr(turn_message, "text", "") or ""
        anchor_parts: List[str] = []
        for block in ("task", "inherited-context"):
            match = re.search(rf"<{block}>.*?</{block}>", text, re.S)
            if match:
                anchor_parts.append(match.group(0))
            text = re.sub(rf"<{block}>.*?</{block}>", "", text, flags=re.S)
        text = re.sub(r"<capability-context>.*?</capability-context>", "", text, flags=re.S)
        for block in (
            "tool-context", "skill-context", "connector-context", "workflow-context",
            "plugin-context", "subagent-context", "memory", "working-memory", "recent-steps",
        ):
            text = re.sub(rf"<{block}>.*?</{block}>", "", text, flags=re.S)
        text = _HTML_COMMENT.sub("", text)

        def turn(body: str) -> List[Message]:
            body = body.strip()
            return [HumanMessage(content=body)] if re.sub(r"<[^>]+>", "", body).strip() else []

        return turn("\n".join(anchor_parts)), turn(text)


context_builder = ContextBuilder()


__all__ = ["ContextBuilder", "context_builder"]
