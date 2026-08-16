"""TieredMemory — shared per-session memory state machine.

Both GeneralMemorySystem and FileSystemMemory share this structure; they differ
only in how a session is persisted (``_render`` → JSON vs HTML).

On every TraceEvent, ``emit`` syncs into four places:
    todos          ← plan / sub-agent steps / MetaAgent subtask lifecycle
    flow_chart     ← same sources (agent call path)
    recent_history ← raw execution log (bounded; overflow → working_memory)
    final_result   ← root agent's end result

Tiers
-----
recent_history : bounded queue of raw records. ``get()`` injects the last
                 ``recent_fetch`` verbatim.
working_memory : bounded queue of LLM summaries. When recent_history overflows
                 ``recent_max``, the oldest ``compact_chunk`` records are handed
                 to the ``compact`` hook and the returned text is appended here.
                 ``get()`` injects the last ``working_fetch`` summaries.

Summarisation is delegated to the ``compact`` hook (list[str] → text) so the
LLM-summary logic lives in exactly one place.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field

from agentevolver.logger import logger
from agentevolver.memory.types import Memory
from agentevolver.message.types import HumanMessage, SystemMessage
from agentevolver.model import model_manager
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.utils import assemble_workspace_path

_FLOW_LABEL_MAX = 80

#: Cap on one remembered entry's detail. Smaller than a tool's own output limit, because
#: memory holds a window of these and every one of them is rendered into every subsequent
#: prompt — what a turn can afford to read once, a prompt cannot afford to carry forever.
_RECORD_DETAIL_MAX = 8_000

#: Share of that cap given to the head when an entry has to be cut.
#:
#: Head-only truncation loses whatever a producer appended last, and what the tool
#: pipeline appends last is the spill locator — the path to the full output. Cutting it
#: off leaves the agent holding an excerpt that says text was dropped and no longer says
#: where it went, which is the exact failure the spill store exists to prevent.
_RECORD_HEAD_SHARE = 0.8

# MetaAgent subtask lifecycle event name → display status.
_SUBTASK_STATUS_MAP: Dict[str, str] = {
    "subtask_dispatch": "running",
    "subtask_done": "done",
    "subtask_failed": "failed",
    "subtask_cancelled": "cancelled",
}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _write_sync(file_path: str, content: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _parse_message_dict(message: Any) -> Dict[str, Any]:
    """Best-effort parse of an event message into a dict.

    Agent step output may be JSON or a Python-dict repr; try JSON first (fast, safe),
    then ``ast.literal_eval`` as a fallback, then give up with an empty dict. Never
    raises, so a malformed message can't break memory ingestion.
    """
    if isinstance(message, dict):
        return message
    if not message or not isinstance(message, str):
        return {}
    for parse in (json.loads, ast.literal_eval):
        try:
            out = parse(message)
            if isinstance(out, dict):
                return out
        except Exception:
            continue
    return {}


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

def _detached(coro, what: str) -> None:
    """Run a background memory task and say so when it fails.

    A task whose handle is dropped takes its exception with it: asyncio reports
    "Task exception was never retrieved" at interpreter shutdown, long after the run that
    lost the data has ended and with nothing naming what was lost. Both of these write
    memory — todos the agent believes it recorded, and the compaction that keeps history
    from growing without bound — so silence here is a run acting on a memory it does not
    have.

    Deliberately does not re-raise. These are fire-and-forget by design: the caller has
    already returned, and there is nobody left to fail.
    """
    task = asyncio.ensure_future(coro)

    def _report(finished: asyncio.Future) -> None:
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:
            logger.warning(f"| ⚠️ Memory {what} failed: {error!r}")

    task.add_done_callback(_report)


class TodoEntry(BaseModel):
    id: str = ""
    description: str = ""
    agent_name: str = ""
    status: str = "pending"


class FlowStep(BaseModel):
    step: int
    label: str
    agents: List[str] = Field(default_factory=list)
    status: str = "pending"
    round: int = 1
    round_label: str = ""


class MemoryRecord(BaseModel):
    """One entry in recent_history."""
    ts: str
    event: str            # short label, e.g. "tool_x result"
    detail: str = ""
    status: str = ""      # "", "running", "done", "failed"
    #: Position of the trace event this record came from. Carried so that folding a run
    #: of records into a summary can say *which* events the summary now stands for —
    #: without it, memory's history and the durable log drift apart with nothing able to
    #: relate them. ``None`` for records with no originating event.
    seq: Optional[int] = None

    def as_line(self) -> str:
        s = f" [{self.status}]" if self.status else ""
        d = f": {self.detail}" if self.detail else ""
        return f"[{self.ts}] {self.event}{s}{d}"


class _SessionState:
    def __init__(self, session_id: str, task: str, file_path: str, working_max: int) -> None:
        self.session_id = session_id
        self.task = task
        self.file_path = file_path

        self.todos: List[TodoEntry] = []
        self.flow_steps: List[FlowStep] = []
        self.recent: Deque[MemoryRecord] = deque()           # compaction controls size
        self.working: Deque[str] = deque(maxlen=working_max)  # drops oldest summary when full
        self.final_result: Optional[str] = None
        self.result_success: bool = True

        self.last_access: float = time.monotonic()  # for LRU eviction
        self._dirty: bool = False                    # unpersisted changes pending
        self._flush_task: Optional[asyncio.Task] = None  # single coalescing writer

        # Buffer tool/skill actions per step until POST_STEP flushes them into the flow chart.
        self._pending_step_actions: Dict[int, List[Dict[str, Any]]] = {}
        # subtask_id → index, for O(1) MetaAgent status updates.
        self._subtask_todo_index: Dict[str, int] = {}
        self._subtask_flow_index: Dict[str, int] = {}

        self._compacting = False
        #: Open compaction bracket: ``{"started_at", "chunks"}`` while one is running,
        #: ``None`` otherwise. Unlike ``_compacting`` — a process-local flag that dies
        #: with the process — this is rendered into the persisted memory artifact, so a
        #: compaction that never finished stays visible in the file afterwards instead
        #: of leaving a silently shortened history and no explanation.
        self.compaction: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()        # guards recent during compaction
        self._write_lock = asyncio.Lock()  # serialises file writes
        self._todos_lock = asyncio.Lock()  # serialises _apply_todos


# ---------------------------------------------------------------------------
# TieredMemory base
# ---------------------------------------------------------------------------

class TieredMemory(Memory):
    """Per-session memory: todos + flowchart + recent/working tiers + result.

    Subclasses define ``_render`` (JSON vs HTML)."""

    base_dir: str = Field(default="")
    model_name: str = Field(default="gpt-4.1")
    compact_hook: str = Field(default="compact", description="Hook name used to summarise overflow.")
    max_todo_length: int = Field(default=80)

    recent_max: int = Field(default=30, description="Compact when recent_history exceeds this.")
    recent_fetch: int = Field(default=10, description="Recent records injected by get().")
    working_max: int = Field(default=10, description="Max working-memory summaries kept.")
    working_fetch: int = Field(default=5, description="Working summaries injected by get().")
    compact_chunk: int = Field(default=10, description="Records consolidated per compaction.")

    persist_debounce: float = Field(default=0.2, description="Seconds to coalesce a burst of events into a single file write per session.")
    max_sessions: int = Field(default=256, description="Max in-memory sessions kept; least-recently-used ones are evicted beyond this.")

    file_ext: str = Field(default="txt", description="Persisted file extension.")

    def __init__(self, base_dir: str = "", **kwargs: Any) -> None:
        super().__init__(
            base_dir=str(assemble_workspace_path(base_dir)) if base_dir else "",
            **kwargs,
        )
        # A fetch window must always be fully backed by raw records.
        assert self.compact_chunk <= self.recent_max, "compact_chunk must be <= recent_max"
        assert self.recent_fetch <= self.recent_max - self.compact_chunk, \
            "recent_fetch must be <= recent_max - compact_chunk"
        self._sessions: Dict[str, _SessionState] = {}
        self._registry_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def emit(self, event: TraceEvent, session_id: str) -> None:
        """Ingest a TraceEvent and sync it into todos / flow / recent / result."""
        state = await self._get_or_create(session_id, event)
        ev = event.event_type
        changed = True

        if ev == TraceEventType.AGENT_START:
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event=f"Agent started: {event.agent_name or ''}",
                detail=(event.input or {}).get("task", ""), status="running", seq=event.seq_no))

        elif ev == TraceEventType.AGENT_END:
            ok = event.success if event.success is not None else (event.metadata.get("success", not bool(event.error)))
            result = event.error if not ok else _as_text(event.message)
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event=f"Agent ended: {event.agent_name or ''}",
                detail=result, status="done" if ok else "failed", seq=event.seq_no))
            if event.metadata.get("is_root", False):
                state.final_result = result
                state.result_success = ok

        elif ev in (TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL):
            ok = event.success if event.success is not None else (event.metadata.get("success", not bool(event.error)))
            detail = event.error if not ok else _as_text(event.message)
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event=f"{event.action_name or event.action_type or 'action'} result",
                detail=detail, status="done" if ok else "failed", seq=event.seq_no))
            # Buffer for the flow chart — flushed on the step's POST_STEP AGENT_CALL.
            if event.step_number is not None:
                state._pending_step_actions.setdefault(event.step_number, []).append({
                    "action_name": event.action_name or event.action_type or "action",
                    "description": event.metadata.get("description") or event.action_name or "",
                    "success": ok,
                    "action_index": event.action_index or 0,
                })

        elif ev == TraceEventType.ERROR:
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event="Error", detail=event.error or _as_text(event.message),
                status="failed", seq=event.seq_no))

        elif ev == TraceEventType.AGENT_CALL:
            changed = self._apply_agent_call(state, event)

        else:
            changed = False

        if changed:
            self._schedule_persist(state)

    async def get(self, session_id: str, short_term_n: Optional[int] = None, **kwargs) -> Optional[str]:
        """Return a markdown memory context string for prompt injection."""
        async with self._registry_lock:
            state = self._sessions.get(session_id)
            if state is not None:
                state.last_access = time.monotonic()
        if state is None:
            return None

        lines: list[str] = []

        working = list(state.working)[-self.working_fetch:]
        if working:
            lines.append("## Working Memory")
            lines += [f"- {s}" for s in working]
            lines.append("")

        n = short_term_n if short_term_n is not None else self.recent_fetch
        recent = list(state.recent)[-n:] if n else list(state.recent)
        if recent:
            lines.append("## Recent Steps")
            lines += [r.as_line() for r in recent]
            lines.append("")

        if state.final_result:
            lines.append("## Final Result")
            lines.append(state.final_result)

        result = "\n".join(lines).strip()
        return result or None

    # ------------------------------------------------------------------
    # AGENT_CALL → todos / flow chart sync
    # ------------------------------------------------------------------

    def _apply_agent_call(self, state: _SessionState, event: TraceEvent) -> bool:
        md = event.metadata
        changed = False
        # Try to parse message as dict for step-level data (reasoning)
        out: Dict[str, Any] = _parse_message_dict(event.message)

        # ── POST_STEP: flush this step's buffered tool/skill actions ──
        if event.step_number is not None and "reasoning" in out:
            step = event.step_number
            reasoning_text = (out.get("reasoning") or "").strip()
            round_label = (reasoning_text.splitlines()[0] if reasoning_text else f"Step {step}")[:_FLOW_LABEL_MAX]
            pending = state._pending_step_actions.pop(step, [])
            for act in sorted(pending, key=lambda a: a["action_index"]):
                label = (act["description"] or act["action_name"])[:_FLOW_LABEL_MAX]
                status = "done" if act["success"] else "failed"
                state.flow_steps.append(FlowStep(
                    step=len(state.flow_steps) + 1, label=label,
                    agents=[act["action_name"]], status=status,
                    round=step, round_label=round_label))
                state.todos.append(TodoEntry(
                    id=f"step{step}-a{act['action_index']}", description=label,
                    agent_name=act["action_name"], status=status))
                changed = True

        # ── MetaAgent subtask lifecycle ──
        if "subtask_event" in md:
            se = md["subtask_event"]
            action = se.get("action", "")
            data = se.get("data", {})
            subtask_id = data.get("subtask_id", "")
            if action == "subtask_planned":
                label = data.get("task", "")[:_FLOW_LABEL_MAX]
                agent_name = data.get("agent_name", "")
                round_no = data.get("round", 1)
                round_label = data.get("round_label", f"Round {round_no}")
                state._subtask_todo_index[subtask_id] = len(state.todos)
                state.todos.append(TodoEntry(id=subtask_id, description=label,
                                             agent_name=agent_name, status="pending"))
                state._subtask_flow_index[subtask_id] = len(state.flow_steps)
                state.flow_steps.append(FlowStep(
                    step=len(state.flow_steps) + 1, label=label, agents=[agent_name],
                    status="pending", round=round_no, round_label=round_label))
                changed = True
            elif action in _SUBTASK_STATUS_MAP:
                status = _SUBTASK_STATUS_MAP[action]
                idx = state._subtask_todo_index.get(subtask_id)
                if idx is not None and idx < len(state.todos):
                    state.todos[idx].status = status
                idx = state._subtask_flow_index.get(subtask_id)
                if idx is not None and idx < len(state.flow_steps):
                    state.flow_steps[idx].status = status
                changed = True

        # ── Explicit snapshots (legacy / external override) ──
        if "todos" in md:
            _detached(self._apply_todos(state, md["todos"]), "todo update")
            changed = True
        if "flow_steps" in md:
            state.flow_steps = [
                FlowStep(step=s.get("step", i + 1), label=s.get("label", "")[:_FLOW_LABEL_MAX],
                         agents=s.get("agents", []), status=s.get("status", "pending"),
                         round=s.get("round", 1), round_label=s.get("round_label", ""))
                for i, s in enumerate(md["flow_steps"])
            ]
            changed = True
        if "note" in md:
            n = md["note"]
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event=n.get("event", event.label),
                detail=n.get("detail", ""), status=n.get("status", "")))
            changed = True
        if "final_result" in md:
            state.final_result = md["final_result"]
            state.result_success = md.get("success", True)
            changed = True

        return changed

    async def _apply_todos(self, state: _SessionState, raw_todos: List[Dict[str, Any]]) -> None:
        """Replace todos from an explicit snapshot, summarising long descriptions."""
        async with state._todos_lock:
            descs = await asyncio.gather(
                *[self._maybe_summarize(t.get("description", "")) for t in raw_todos],
                return_exceptions=True,
            )
            state.todos = [
                TodoEntry(id=t.get("id", ""),
                          description=d if isinstance(d, str) else t.get("description", ""),
                          agent_name=t.get("agent_name", ""), status=t.get("status", "pending"))
                for t, d in zip(raw_todos, descs)
            ]
            await self._persist(state)

    async def _maybe_summarize(self, description: str) -> str:
        if len(description) <= self.max_todo_length:
            return description
        try:
            response = await model_manager(name=self.model_name, input={"messages": [
                SystemMessage(content="You are a concise summariser."),
                HumanMessage(content=(
                    f"Summarise this task description in at most {self.max_todo_length} characters. "
                    f"Return ONLY the summary, no extra text.\n\n{description}")),
            ]})
            return response.message.strip()[: self.max_todo_length]
        except Exception as e:
            logger.warning(f"| ⚠️ {self.name}: todo summarisation failed ({e}), truncating")
            return description[: self.max_todo_length]

    # ------------------------------------------------------------------
    # recent → working compaction (via the compact hook)
    # ------------------------------------------------------------------

    def _append_recent(self, state: _SessionState, record: MemoryRecord) -> None:
        # Bound the entry before it is stored. The window that holds these is small, but a
        # single entry has no natural size, and a recorded tool result *is* whatever the
        # tool returned: one `strings` call against a binary put 14,419,441 characters into
        # this deque, and the whole window is rendered into every prompt afterwards, so the
        # run then asked for 4.3 million tokens against a limit of 1,048,576 and died of
        # consecutive 400s.
        #
        # The tools clip their own output too, which is where it matters most for what the
        # agent reads. This is the backstop, so that no future tool — or a tool whose limit
        # is raised — can make the prompt unsendable.
        #
        # Head *and* tail, because the last thing in a bounded tool result is the
        # reference to where the unbounded original was saved. Keeping only the head
        # would drop it and leave a note about missing text with no way to reach it.
        if record.detail and len(record.detail) > _RECORD_DETAIL_MAX:
            head = int(_RECORD_DETAIL_MAX * _RECORD_HEAD_SHARE)
            tail = _RECORD_DETAIL_MAX - head
            dropped = len(record.detail) - _RECORD_DETAIL_MAX
            record = record.model_copy(update={
                "detail": (
                    f"{record.detail[:head]}\n"
                    f"[... {dropped:,} more characters not kept in memory ...]\n"
                    f"{record.detail[-tail:]}"
                )
            })
        state.recent.append(record)
        if len(state.recent) > self.recent_max and not state._compacting:
            _detached(self._compact(state), "compaction")

    async def compact(self, session_id: str) -> bool:
        """Fold one chunk now, without waiting for ``recent_max`` to be exceeded.

        The ordinary trigger counts records, which is the wrong unit when something else
        has already measured the problem: a request that does not fit is over a token
        budget, and thirty small records can sit under the count while three large ones
        sit over the budget. This lets whoever measured it ask for room directly.

        Never folds below ``recent_fetch`` — that is how many records reach the prompt, so
        folding past it would buy space by removing what the next step is about to read.
        """
        state = self._sessions.get(session_id)
        if state is None or state._compacting:
            return False
        if len(state.recent) <= self.recent_fetch:
            return False
        before = len(state.recent)
        await self._compact(state, down_to=max(self.recent_fetch, before - self.compact_chunk))
        return len(state.recent) < before

    async def _compact(self, state: _SessionState, *, down_to: Optional[int] = None) -> None:
        """Fold the oldest history into summaries, as one recorded transaction.
 
        The bracket is what makes a crash legible. ``state.compaction`` is set and
        persisted *before* any records leave ``recent``, and cleared only after the last
        chunk has been summarised and the result written. A memory artifact carrying an
        open bracket therefore says exactly one thing: a compaction started and never
        finished, so the history below it may be short by whatever that run had claimed.
        Clearing the marker last is the whole point — clearing it first would make a
        crashed compaction indistinguishable from a completed one.

        This is a diagnostic, not a recovery: these files are written, never read back,
        so nothing resumes an interrupted compaction. What the bracket buys is that the
        gap stops being silent, for whoever — operator or agent — reads the memory next.

        Each chunk is summarised before the next is taken, and a chunk whose summary
        fails is put back, so a partial run leaves history shorter but never holed.
        """
        state._compacting = True
        floor = self.recent_max if down_to is None else max(0, int(down_to))
        outcome = "ok"
        chunks_done = 0
        try:
            state.compaction = {"started_at": _ts(), "chunks": 0}
            await self._persist(state)

            while len(state.recent) > floor:
                async with state._lock:
                    k = min(self.compact_chunk, len(state.recent) - floor, len(state.recent))
                    chunk = [state.recent.popleft() for _ in range(k)]
                items = [r.as_line() for r in chunk]
                existing = state.working[-1] if state.working else ""
                try:
                    text = await self._summarise(items, existing)
                except asyncio.CancelledError:
                    async with state._lock:
                        state.recent.extendleft(reversed(chunk))
                    raise
                except Exception as error:
                    # The summariser reaching the model and failing is not the same as
                    # it returning nothing, and saying so is the difference between
                    # "the model is unreachable" and "there was nothing worth saying".
                    async with state._lock:
                        state.recent.extendleft(reversed(chunk))
                    outcome = "summary"
                    logger.warning(f"| ⚠️ {self.name}: summariser failed ({error})")
                    break
                if not text:
                    async with state._lock:  # lossless: restore and stop
                        state.recent.extendleft(reversed(chunk))
                    outcome = "empty"
                    break
                state.working.append(text)
                chunks_done += 1
                state.compaction = {"started_at": state.compaction["started_at"], "chunks": chunks_done}
                await self._record_fold(state, chunk, text)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as error:
            outcome = "failed"
            logger.warning(f"| ⚠️ {self.name}: compaction failed ({error})")
        finally:
            # Released last, and persisted with the release, so the bracket on disk
            # closes only once the work behind it is durable.
            state.compaction = None
            state._compacting = False
            try:
                await self._persist(state)
            except Exception as error:  # noqa: BLE001 — the compaction itself already happened
                logger.warning(f"| ⚠️ {self.name}: could not persist after compaction ({error})")
            if outcome == "ok":
                logger.info(
                    f"| 🗜️ {self.name}: compacted {chunks_done} chunk(s) for {state.session_id}"
                )
            else:
                logger.info(
                    f"| 🗜️ {self.name}: compaction stopped ({outcome}) after {chunks_done} "
                    f"chunk(s) for {state.session_id}"
                )

    async def _record_fold(self, state: _SessionState, chunk: list, summary: str) -> None:
        """Tell the durable log that one summary now stands for a run of its events.

        Without this the two records of a session disagree after every compaction:
        memory's history has a summary where a dozen records used to be, and the trace
        log still has the dozen with nothing marking them as folded. Neither is wrong on
        its own and there is no way to line them up.

        The event replaces the range on the *surface* while the originals stay in the
        log untouched, so the folded records remain readable — the summary shadows them
        rather than deleting them, and cites every seq it shadowed.

        Best-effort: a compaction that already happened must not be undone because its
        bookkeeping could not be written.
        """
        seqs = [r.seq for r in chunk if r.seq is not None]
        if not seqs:
            return  # nothing to cite: these records predate sequence numbering
        try:
            from agentevolver.trace import replace_op, trace_manager
            from agentevolver.trace.types import TraceEvent, TraceEventType

            # Cite the whole span, not just these records. Memory keeps one record per
            # *result*, while the surface also carries the assistant turn that produced
            # it — so citing only what memory holds would under-claim, and the fold would
            # refuse the log. The trace owns the surface; ask it what is in the range.
            start, end = min(seqs), max(seqs)
            shadowed = trace_manager.surface_span(state.session_id, start, end)
            if not shadowed:
                # The surface is live state. Empty means this process did not emit these
                # events, so the span cannot be verified — and a replacement that cannot
                # cite what it shadows is worse than no record of the fold at all.
                logger.debug(
                    f"| 🗜️ {self.name}: no live surface for {state.session_id}; fold not recorded"
                )
                return

            await trace_manager.emit(TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=state.session_id,
                label="compaction summary",
                message=summary,
                success=True,
                surface_op=replace_op(start, end),
                source_event_seqs=shadowed,
                metadata={"type": "compaction", "records": len(chunk)},
            ))
        except Exception as error:  # noqa: BLE001 — the fold itself already succeeded
            logger.warning(f"| ⚠️ {self.name}: could not record the fold in the trace ({error})")

    async def _summarise(self, items: list[str], existing: str) -> str:
        from agentevolver.hook import hook_manager, HookEvent
        res = await hook_manager(name=self.compact_hook, input={
            "event": HookEvent.ON_CALL, "items": items,
            "existing_summary": existing, "model_name": self.model_name,
        })
        return (res.output or "").strip()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _schedule_persist(self, state: _SessionState) -> None:
        """Mark the session dirty and ensure exactly one coalescing writer is running.

        Called on the hot path (every event). Instead of spawning a full-file write per
        event, a single per-session flush task absorbs bursts and writes once per debounce
        window — far fewer full re-renders/overwrites. Safe under asyncio's single thread:
        the dirty flag and task check/create happen without an intervening await.
        """
        state._dirty = True
        t = state._flush_task
        if t is None or t.done():
            state._flush_task = asyncio.create_task(self._flush_loop(state))

    async def _flush_loop(self, state: _SessionState) -> None:
        """Drain the dirty flag, coalescing a burst of events into as few writes as possible."""
        try:
            while state._dirty:
                state._dirty = False               # events during the window re-set this → loop again
                if self.persist_debounce > 0:
                    await asyncio.sleep(self.persist_debounce)
                await self._persist(state)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"| ⚠️ {self.name}: persist flush failed ({e})")

    async def _persist(self, state: _SessionState) -> None:
        if not self.base_dir:
            return
        async with state._write_lock:
            content = self._render(state)          # synchronous + atomic → consistent snapshot
            await asyncio.to_thread(_write_sync, state.file_path, content)

    def _render(self, state: _SessionState) -> str:
        """Serialise a session to its on-disk representation. Subclasses override."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_or_create(self, session_id: str, event: TraceEvent) -> _SessionState:
        victims: List[_SessionState] = []
        async with self._registry_lock:
            if session_id not in self._sessions:
                task = ""
                if event.event_type == TraceEventType.AGENT_START:
                    task = (event.input or {}).get("task", "")
                agent_name = (event.agent_name or "").strip()
                # MetaAgent-dispatched sub-agents already carry the agent name in their
                # session_id (e.g. "code_agent-<id>"); don't prepend it again or the file
                # name becomes "code_agent_code_agent-<id>".
                if agent_name and not (
                    session_id == agent_name or session_id.startswith(f"{agent_name}-")
                ):
                    stem = f"{agent_name}_{session_id}"
                else:
                    stem = session_id
                file_path = os.path.join(self.base_dir, f"{stem}.{self.file_ext}") if self.base_dir else ""
                self._sessions[session_id] = _SessionState(
                    session_id=session_id, task=task, file_path=file_path,
                    working_max=self.working_max)
                logger.info(f"| 📄 {self.name}: created session {session_id}")
            state = self._sessions[session_id]
            state.last_access = time.monotonic()
            # Bound in-memory sessions: evict least-recently-used ones (never the current
            # one). Collect victims under the lock; finalize them AFTER releasing it so a
            # victim's final flush can't deadlock against this registry lock.
            if len(self._sessions) > self.max_sessions:
                for sid, st in sorted(self._sessions.items(), key=lambda kv: kv[1].last_access):
                    if len(self._sessions) <= self.max_sessions:
                        break
                    if sid == session_id:
                        continue
                    victims.append(self._sessions.pop(sid))
        for st in victims:
            await self._evict(st)
        return state

    async def _evict(self, state: _SessionState) -> None:
        """Flush a session's final state to disk, then drop its coalescing writer.

        Called only after the session was removed from ``_sessions`` (under the registry
        lock), so no new events can reach it while we finalize.
        """
        try:
            await self._persist(state)
        except Exception as e:
            logger.warning(f"| ⚠️ {self.name}: final persist on evict failed ({e})")
        t = state._flush_task
        if t is not None and not t.done():
            t.cancel()
        logger.info(f"| 🧹 {self.name}: evicted LRU session {state.session_id}")
