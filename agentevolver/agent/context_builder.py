"""Build the model-facing conversation from prompt scaffolding and Trace.

The prompt renderer owns instructions and live state. Trace owns what happened. This
module is the only place that combines those two sources into a request history.
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from agentevolver.message.types import HumanMessage, Message, ToolMessage
from agentevolver.trace.derive import _marker, derive_messages
from agentevolver.trace.surface import fold_surface
from agentevolver.trace.types import TraceEventType


class ContextBuilder:
    """Create a cache-friendly task/checkpoint/conversation/live-state layout."""

    TOOL_RESULT_MAX = 8_000

    def build(self, rendered: List[Message], events: List[Any], ctx: Any) -> List[Message]:
        system = [m for m in rendered if getattr(m, "role", "") == "system"]
        derived = derive_messages(events)
        if not derived:
            return rendered

        anchor, live = self.split_rendered_turn(rendered)
        task = self._task(events)
        checkpoints = self._checkpoints(events)
        recent = self._bound_tool_results(
            self._recent_messages(derived, task, checkpoints)
        )

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
        checkpoint = [
            HumanMessage(content=f"<memory-checkpoint>\n{text}\n</memory-checkpoint>")
            for text in checkpoints[-1:]
        ]

        frozen = checkpoint + recent
        if frozen:
            frozen[-1].cache = True
        return system + anchor + frozen + live

    @staticmethod
    def _task(events: List[Any]) -> str:
        for event in events:
            if event.event_type == TraceEventType.AGENT_START:
                return str((event.input or {}).get("task") or "")
        return ""

    @staticmethod
    def _checkpoints(events: List[Any]) -> List[str]:
        surface = set(fold_surface(events)["nodes"])
        return [
            str(event.message or "")
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
        """Remove context-bearing user turns; they are represented once in the anchor."""
        checkpoint_set = set(checkpoints)
        recent: List[Message] = []
        task_removed = False
        for message in messages:
            if getattr(message, "role", "") == "user":
                text = getattr(message, "text", "") or ""
                if not task_removed and text == task:
                    task_removed = True
                    continue
                if text in checkpoint_set:
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

        def turn(body: str) -> List[Message]:
            body = body.strip()
            return [HumanMessage(content=body)] if re.sub(r"<[^>]+>", "", body).strip() else []

        return turn("\n".join(anchor_parts)), turn(text)

    def _bound_tool_results(self, messages: List[Message]) -> List[Message]:
        """Keep prompt history bounded while retaining each result's head and locator tail."""
        bounded: List[Message] = []
        for message in messages:
            if not isinstance(message, ToolMessage) or len(message.content) <= self.TOOL_RESULT_MAX:
                bounded.append(message)
                continue
            head = int(self.TOOL_RESULT_MAX * 0.8)
            tail = self.TOOL_RESULT_MAX - head
            dropped = len(message.content) - self.TOOL_RESULT_MAX
            content = (
                f"{message.content[:head]}\n"
                f"[... {dropped:,} characters omitted from active context ...]\n"
                f"{message.content[-tail:]}"
            )
            bounded.append(message.model_copy(update={"content": content}))
        return bounded


context_builder = ContextBuilder()

__all__ = ["ContextBuilder", "context_builder"]
