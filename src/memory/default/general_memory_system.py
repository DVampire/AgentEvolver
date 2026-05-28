"""
GeneralMemorySystem — two-layer per-task memory backed by TraceEvents.

Architecture
------------
ShortTermMemory  : bounded deque of recent TraceEvents, no LLM required.
WorkingMemory    : LLM-generated summaries triggered every N short-term events.

Interface (same as FileSystemMemory):
    await mem.emit(event, session_id)   # ingest a TraceEvent; never blocks caller
    text  = await mem.get(session_id)   # markdown summary for prompt injection

Sessions are task-scoped and keyed by session_id.  Working-memory summarisation
is flushed automatically when an AGENT_END event is received — no end_session()
call needed.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Dict, List, Optional, Deque

from pydantic import Field

from src.logger import logger
from src.memory.types import Memory
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager
from src.registry import MEMORY_SYSTEM
from src.trace.types import TraceEvent, TraceEventType
from src.utils import dedent


# ---------------------------------------------------------------------------
# ShortTermMemory
# ---------------------------------------------------------------------------

class ShortTermMemory:
    """Bounded sliding window of recent TraceEvents. No LLM, pure append."""

    def __init__(self, maxsize: int = 50) -> None:
        self._events: Deque[TraceEvent] = deque(maxlen=maxsize)

    def append(self, event: TraceEvent) -> None:
        self._events.append(event)

    def recent(self, n: Optional[int] = None) -> List[TraceEvent]:
        events = list(self._events)
        if n is not None:
            events = events[-n:]
        return events

    def __len__(self) -> int:
        return len(self._events)


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------

class WorkingMemory:
    """LLM-generated summaries, refreshed every trigger_every events."""

    def __init__(self, model_name: str = "gpt-4.1-mini", max_summaries: int = 10, trigger_every: int = 10) -> None:
        self.model_name = model_name
        self.max_summaries = max_summaries
        self.trigger_every = trigger_every

        self._summaries: List[str] = []
        self._pending: List[TraceEvent] = []   # events not yet summarised
        self._lock = asyncio.Lock()

    def stage(self, event: TraceEvent) -> bool:
        """Stage an event. Returns True when the trigger threshold is reached."""
        # Only stage semantically meaningful events
        if event.event_type in (
            TraceEventType.AGENT_START,
            TraceEventType.AGENT_CALL,
            TraceEventType.AGENT_END,
            TraceEventType.TOOL_CALL,
            TraceEventType.SKILL_CALL,
        ):
            self._pending.append(event)
        return len(self._pending) >= self.trigger_every

    async def maybe_summarise(self) -> None:
        """Trigger summarisation if threshold reached. Safe to call concurrently."""
        if len(self._pending) < self.trigger_every:
            return
        async with self._lock:
            # Re-check under lock
            if len(self._pending) < self.trigger_every:
                return
            pending = self._pending[:]
            self._pending.clear()

        await self._summarise(pending)

    async def force_summarise(self) -> None:
        """Summarise whatever is pending (called on session end)."""
        async with self._lock:
            if not self._pending:
                return
            pending = self._pending[:]
            self._pending.clear()
        await self._summarise(pending)

    async def _summarise(self, events: List[TraceEvent]) -> None:
        if not events:
            return

        events_text = "\n".join(self._format_event(e) for e in events)
        existing = "\n".join(f"- {s}" for s in self._summaries) if self._summaries else "(none yet)"

        prompt = dedent(f"""You are summarising agent execution steps for future context injection.

            Existing summaries:
            {existing}

            New execution events:
            {events_text}

            Write 1-3 concise bullet points capturing the key decisions, results, or failures
            from the new events that would help an agent avoid repeating mistakes or reuse
            successful approaches. Do NOT repeat what is already in existing summaries.
            Respond with plain bullet points only (no headers, no JSON).""")

        try:
            response = await model_manager(
                name=self.model_name,
                input={
                    "messages": [
                        SystemMessage(content="You are a concise memory summarisation assistant."),
                        HumanMessage(content=prompt),
                    ],
                },
            )
            text = response.message.strip() if response.success else ""
            if text:
                new_bullets = [line.lstrip("-• ").strip() for line in text.splitlines() if line.strip()]
                self._summaries.extend(new_bullets)
                # Keep most recent max_summaries entries
                if len(self._summaries) > self.max_summaries:
                    self._summaries = self._summaries[-self.max_summaries:]
                logger.debug(f"| 🧠 WorkingMemory: added {len(new_bullets)} summaries (total={len(self._summaries)})")
        except Exception as e:
            logger.warning(f"| ⚠️ WorkingMemory summarisation failed: {e}")

    def summaries(self) -> List[str]:
        return list(self._summaries)

    @staticmethod
    def _format_event(e: TraceEvent) -> str:
        parts = [f"[{e.event_type.value}] agent={e.agent_name or '-'} step={e.step_number or '-'}"]
        if e.label:
            parts.append(e.label)
        if e.output and isinstance(e.output, str):
            parts.append(f"output: {e.output.replace(chr(10), ' ')}")
        elif e.output and isinstance(e.output, dict):
            memory = e.output.get("memory")
            next_goal = e.output.get("next_goal")
            if memory:
                parts.append(f"memory: {memory.replace(chr(10), ' ')}")
            elif next_goal:
                parts.append(f"goal: {next_goal.replace(chr(10), ' ')}")
        elif e.error:
            parts.append(f"error: {e.error}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Per-session state
# ---------------------------------------------------------------------------

class _SessionState:
    def __init__(self, short_term: ShortTermMemory, working: WorkingMemory) -> None:
        self.short_term = short_term
        self.working = working


# ---------------------------------------------------------------------------
# GeneralMemorySystem
# ---------------------------------------------------------------------------

@MEMORY_SYSTEM.register_module(force=True)
class GeneralMemorySystem(Memory):
    """Two-layer per-session memory: short-term (recent TraceEvents) + working (LLM summaries)."""

    short_term_maxsize: int = Field(default=50, description="Max TraceEvents kept in short-term memory per session.")
    working_max_summaries: int = Field(default=10, description="Max summary bullets kept in working memory.")
    working_trigger_every: int = Field(default=10, description="Number of staged events before LLM summarisation.")
    model_name: str = Field(default="gpt-4.1-mini", description="Model used for working-memory summarisation.")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sessions: Dict[str, _SessionState] = {}
        self._cache_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal session management
    # ------------------------------------------------------------------

    async def _get_or_create(self, session_id: str) -> _SessionState:
        async with self._cache_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = _SessionState(
                    short_term=ShortTermMemory(maxsize=self.short_term_maxsize),
                    working=WorkingMemory(
                        model_name=self.model_name,
                        max_summaries=self.working_max_summaries,
                        trigger_every=self.working_trigger_every,
                    ),
                )
            return self._sessions[session_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def emit(self, event: TraceEvent, session_id: str) -> None:
        """Ingest a TraceEvent. Never blocks the caller."""
        state = await self._get_or_create(session_id)
        state.short_term.append(event)
        should_summarise = state.working.stage(event)
        if should_summarise:
            asyncio.create_task(state.working.maybe_summarise())
        # On task end, flush pending summaries in the background.
        if event.event_type == TraceEventType.AGENT_END:
            asyncio.create_task(state.working.force_summarise())

    async def get(self, session_id: str, short_term_n: Optional[int] = 10, **kwargs) -> Optional[str]:
        """Return a formatted markdown memory context string for prompt injection.

        Args:
            session_id: The session to retrieve memory for.
            short_term_n: How many recent short-term events to include (None = all).
        """
        async with self._cache_lock:
            state = self._sessions.get(session_id)
        if state is None:
            return None

        lines: List[str] = []

        # --- Working memory (summaries) ---
        summaries = state.working.summaries()
        if summaries:
            lines.append("## Working Memory")
            for s in summaries:
                lines.append(f"- {s}")
            lines.append("")

        # --- Short-term memory (recent events) ---
        recent = state.short_term.recent(n=short_term_n)
        if recent:
            lines.append("## Recent Steps")
            for e in recent:
                lines.append(f"- {WorkingMemory._format_event(e)}")
            lines.append("")

        result = "\n".join(lines).strip()
        return result or None
