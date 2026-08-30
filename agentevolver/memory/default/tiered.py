"""TieredMemory — shared per-session memory state machine.

Both GeneralMemorySystem and FileSystemMemory share this structure; they differ
only in how a session is persisted (``_render`` → JSON vs HTML).

On every TraceEvent, ``emit`` syncs into four places:
    todos          ← plan / sub-agent steps / MetaAgent subtask lifecycle
    flow_chart     ← same sources (agent call path)
    recent_history ← raw execution log (folded under measured token pressure)
    final_result   ← root agent's end result

Tiers
-----
recent_history : queue of raw records. ``get()`` injects the last
                 ``recent_fetch`` verbatim.
working_memory : bounded queue of LLM summaries. When the agent measures high
                 request pressure, old records are handed to the ``compact`` hook
                 and the returned text is appended here.
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

from agentevolver.paths import path_manager

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
    step: Optional[int] = None
    agent_name: str = ""
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
        self._pending_action_inputs: Dict[tuple[int, int], Dict[str, Any]] = {}
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
        self.checkpoint_seq: Optional[int] = None
        self._lock = asyncio.Lock()        # guards recent during compaction
        self._write_lock = asyncio.Lock()  # serialises file writes


# ---------------------------------------------------------------------------
# TieredMemory base
# ---------------------------------------------------------------------------

class TieredMemory(Memory):
    """Per-session memory: todos + flowchart + recent/working tiers + result.

    Subclasses define ``_render`` (JSON vs HTML)."""

    base_dir: str = Field(default="")
    model_name: str = Field(default="gpt-4.1")
    compact_hook: str = Field(default="compact", description="Hook name used to summarise overflow.")

    recent_max: int = Field(default=30, description="Retention floor for direct/legacy compaction.")
    recent_fetch: int = Field(default=10, description="Recent records injected by get().")
    working_max: int = Field(default=10, description="Max working-memory summaries kept.")
    #: Per-entry detail cap. Every recent record is re-rendered into every subsequent prompt
    #: and re-read UNCACHED each step (the live state sits past the cache breakpoint), so a
    #: window of big raw outputs (a file dump, a long listing) is the main reducible input
    #: cost. Lower it to trim that at the cost of detail; a truncated entry keeps a head and a
    #: tail (the tail carries the spill locator to the full output).
    record_detail_max: int = Field(default=_RECORD_DETAIL_MAX,
                                   description="Max characters kept for one recent entry's detail.")
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
                detail=(event.input or {}).get("task", ""), status="running", seq=event.seq_no,
                agent_name=event.agent_name or ""))

        elif ev == TraceEventType.AGENT_END:
            ok = event.success if event.success is not None else (event.metadata.get("success", not bool(event.error)))
            result = event.error if not ok else _as_text(event.message)
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event=f"Agent ended: {event.agent_name or ''}",
                detail=result, status="done" if ok else "failed", seq=event.seq_no,
                agent_name=event.agent_name or ""))
            if event.metadata.get("is_root", False):
                state.final_result = result
                state.result_success = ok

        elif ev in (TraceEventType.TOOL_START, TraceEventType.SKILL_START):
            if event.step_number is not None:
                state._pending_action_inputs[
                    (event.step_number, event.action_index or 0)
                ] = dict(event.input or {})
            changed = False

        elif ev in (TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL):
            ok = event.success if event.success is not None else (event.metadata.get("success", not bool(event.error)))
            detail = event.error if not ok else _as_text(event.message)
            action_input = state._pending_action_inputs.pop(
                (event.step_number or 0, event.action_index or 0), None
            )
            if action_input:
                detail = (
                    f"Input: {json.dumps(action_input, ensure_ascii=False, sort_keys=True, default=str)}\n"
                    f"Result: {detail}"
                )
            self._append_recent(state, MemoryRecord(
                ts=_ts(), event=f"{event.action_name or event.action_type or 'action'} result",
                detail=detail, status="done" if ok else "failed", seq=event.seq_no,
                step=event.step_number, agent_name=event.agent_name or ""))
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
                status="failed", seq=event.seq_no, agent_name=event.agent_name or ""))

        elif ev == TraceEventType.AGENT_CALL:
            # Every closed assistant turn needs a retention handle, including a turn
            # that used no capability. Otherwise keep_steps=2 really means "two recent
            # tool-using steps" and assistant-only turns can grow forever on the Trace
            # surface. Store only model-visible text; private reasoning remains outside
            # memory checkpoints.
            self._append_recent(state, MemoryRecord(
                ts=_ts(),
                event=f"Agent step {event.step_number or 0}",
                detail=event.assistant_text or "",
                status="done",
                seq=event.seq_no,
                step=event.step_number,
                agent_name=event.agent_name or "",
            ))
            self._apply_agent_call(state, event)
            changed = True

        else:
            changed = False

        if changed:
            self._schedule_persist(state)

    async def get(self, session_id: str, short_term_n: Optional[int] = None,
                  section: str = "all", **kwargs) -> Optional[str]:
        """Return a markdown memory context string for prompt injection.

        ``section`` selects which tier to render, so a caller that places the two
        tiers in different parts of the prompt can fetch them apart:

          - ``"all"``      — Working Memory + Recent Steps + Final Result (default;
                             byte-identical to the pre-split single block).
          - ``"stable"``   — only Working Memory (append-only compacted summaries;
                             byte-stable between compactions, so it belongs in the
                             cached prefix of the turn).
          - ``"volatile"`` — Recent Steps + Final Result (the sliding window that
                             changes every step; kept out of the cached prefix).

        Splitting the two lets the stable tier ride in the request's cache breakpoint
        instead of being re-read in full on every step.
        """
        async with self._registry_lock:
            state = self._sessions.get(session_id)
            if state is not None:
                state.last_access = time.monotonic()
        if state is None:
            return None

        want_stable = section in ("all", "stable")
        want_volatile = section in ("all", "volatile")

        lines: list[str] = []

        if want_stable:
            working = list(state.working)[-self.working_fetch:]
            if working:
                lines.append("## Working Memory")
                lines += [f"- {s}" for s in working]
                lines.append("")

        if want_volatile:
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
        out: Dict[str, Any] = _parse_message_dict(event.message)

        # ── POST_STEP: flush this step's buffered tool/skill actions ──
        if event.step_number is not None:
            step = event.step_number
            reasoning_text = (event.reasoning or out.get("reasoning") or "").strip()
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
        if record.detail and len(record.detail) > self.record_detail_max:
            head = int(self.record_detail_max * _RECORD_HEAD_SHARE)
            tail = self.record_detail_max - head
            dropped = len(record.detail) - self.record_detail_max
            record = record.model_copy(update={
                "detail": (
                    f"{record.detail[:head]}\n"
                    f"[... {dropped:,} more characters not kept in memory ...]\n"
                    f"{record.detail[-tail:]}"
                )
            })
        state.recent.append(record)

    async def compact(self, session_id: str, *, keep_steps: Optional[int] = None) -> bool:
        """Fold old closed steps after the caller measured request pressure.

        Request size, rather than record count, determines when this is called: thirty
        small records can fit while three large ones can exceed the same token budget.

        With ``keep_steps``, retention is measured in complete logical steps instead of
        raw records. A parallel tool batch therefore stays intact and old signed reasoning,
        calls and results disappear together through the Trace surface replacement.
        """
        state = self._sessions.get(session_id)
        if state is None or state._compacting:
            return False
        floor = self._retention_floor(state, keep_steps)
        if floor is None or len(state.recent) <= floor:
            return False
        before = len(state.recent)
        # One pressure event creates one coherent checkpoint and leaves only the live
        # tail. `_compact` still batches summarizer inputs by compact_chunk internally.
        await self._compact(state, down_to=floor)
        return len(state.recent) < before

    def _retention_floor(
        self, state: _SessionState, keep_steps: Optional[int]
    ) -> Optional[int]:
        """Number of trailing records that represent the requested complete steps."""
        if keep_steps is None:
            return self.recent_fetch
        keep_steps = max(1, int(keep_steps))
        records = list(state.recent)
        ordered_steps: list[tuple[str, int]] = []
        for record in records:
            key = (record.agent_name, record.step) if record.step is not None else None
            if key is not None and key not in ordered_steps:
                ordered_steps.append(key)
        if len(ordered_steps) <= keep_steps:
            return None
        retained = set(ordered_steps[-keep_steps:])
        first = next(
            index for index, record in enumerate(records)
            if (record.agent_name, record.step) in retained
        )
        return len(records) - first

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
        # The callers check this flag too, but they check it and then hand the work to a
        # detached task — and between those two the loop can run, append another record,
        # and pass the same check. Two compactions then interleaved: one's `finally`
        # cleared `state.compaction` while the other was reading it, which surfaced as
        # `compaction failed ('NoneType' object is not subscriptable)` and a chunk put
        # back. Re-checking here is what closes it: from this line to the assignment
        # below there is no await, so the second arrival sees the flag already set.
        if state._compacting:
            return
        state._compacting = True
        floor = self.recent_max if down_to is None else max(0, int(down_to))
        outcome = "ok"
        chunks_done = 0
        # This run's own start time. It was read back out of `state.compaction` on every
        # chunk, which made a shared, deliberately-cleared field load-bearing for a fact
        # that never changes and belongs to this call.
        started_at = _ts()
        native_checkpoint: Optional[Dict[str, Any]] = None
        try:
            state.compaction = {"started_at": started_at, "chunks": 0}
            await self._persist(state)

            # Codex-style native compaction is prepared once for the whole window, not
            # once per internal summarizer chunk.  The readable checkpoint below remains
            # the portable fallback for chat providers and inspection.
            records = list(state.recent)
            folding = records[: max(0, len(records) - floor)]
            if folding:
                native_checkpoint = await self._native_checkpoint(state, folding)

            # Claude's server-side block already contains the provider's canonical
            # summary for the entire fold window. Install it in one atomic replacement;
            # asking a second model to summarize that summary would add latency and drift.
            native_summary = str((native_checkpoint or {}).get("summary") or "").strip()
            if native_summary:
                async with state._lock:
                    chunk = [
                        state.recent.popleft()
                        for _ in range(max(0, len(state.recent) - floor))
                    ]
                existing = state.working[-1] if state.working else ""
                state.working.clear()
                state.working.append(native_summary)
                chunks_done = 1
                state.compaction = {"started_at": started_at, "chunks": chunks_done}
                await self._record_fold(
                    state,
                    chunk,
                    native_summary,
                    existing=existing,
                    native=native_checkpoint,
                )

            while not native_summary and len(state.recent) > floor:
                async with state._lock:
                    k = min(self.compact_chunk, len(state.recent) - floor, len(state.recent))
                    # Never split one parallel tool batch across a checkpoint. Provider
                    # protocols require every result to stay with its assistant tool call.
                    records = list(state.recent)
                    while (
                        k < len(records)
                        and records[k - 1].step is not None
                        and records[k].step == records[k - 1].step
                    ):
                        k += 1
                    chunk = [state.recent.popleft() for _ in range(k)]
                items = self._summary_items(state, chunk)
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
                # Each summary is a complete replacement checkpoint, not another
                # fragment the prompt must carry forever.
                state.working.clear()
                state.working.append(text)
                chunks_done += 1
                state.compaction = {"started_at": started_at, "chunks": chunks_done}
                final_chunk = len(state.recent) <= floor
                await self._record_fold(
                    state,
                    chunk,
                    text,
                    existing=existing,
                    native=native_checkpoint if final_chunk else None,
                )
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

    def _summary_items(self, state: _SessionState, chunk: List[MemoryRecord]) -> List[str]:
        """Render complete closed turns from Trace for checkpoint generation.

        MemoryRecord is a useful retention/UI projection, but it is not the conversation:
        parallel calls are independent records and their call events live only in Trace.
        Compaction is therefore sourced from the append-only Trace and grouped by logical
        step. Private reasoning is intentionally excluded; only model-visible assistant
        text and exact call inputs/results are summarized.
        """
        try:
            from agentevolver.trace import trace_manager

            events = trace_manager.events(state.session_id) or []
        except Exception:  # pragma: no cover - the record fallback is deterministic
            events = []

        turns = {
            (record.agent_name, record.step)
            for record in chunk if record.step is not None
        }
        direct = {record.seq for record in chunk if record.seq is not None}
        lines: List[str] = []

        def clipped(value: Any) -> str:
            text = _as_text(value)
            if len(text) <= self.record_detail_max:
                return text
            head = int(self.record_detail_max * _RECORD_HEAD_SHARE)
            tail = self.record_detail_max - head
            return (
                f"{text[:head]}\n[... {len(text) - self.record_detail_max:,} "
                f"characters omitted ...]\n{text[-tail:]}"
            )

        for event in events:
            seq = event.seq_no
            step = event.step_number
            if event.event_type == TraceEventType.AGENT_START and seq in direct:
                lines.append(
                    f"[source_seq={seq}] user task: "
                    f"{clipped((event.input or {}).get('task', ''))}"
                )
            in_turn = any(
                record_step == step and (not agent or agent == (event.agent_name or ""))
                for agent, record_step in turns
            )
            if in_turn and event.event_type == TraceEventType.AGENT_CALL:
                visible = event.assistant_text or ""
                if visible.strip():
                    lines.append(
                        f"[source_seq={seq} step={step}] assistant: {clipped(visible)}"
                    )
            elif in_turn and event.event_type in {
                TraceEventType.TOOL_START,
                TraceEventType.SKILL_START,
            }:
                arguments = json.dumps(
                    event.input or {}, ensure_ascii=False, sort_keys=True, default=str
                )
                lines.append(
                    f"[source_seq={seq} step={step}] call {event.action_name}: "
                    f"{clipped(arguments)}"
                )
            elif in_turn and event.event_type in {
                TraceEventType.TOOL_CALL,
                TraceEventType.SKILL_CALL,
            }:
                result = event.message if event.success else (event.error or event.message)
                status = "ok" if event.success else "error"
                lines.append(
                    f"[source_seq={seq} step={step}] result {event.action_name} "
                    f"({status}): {clipped(result)}"
                )

        return lines or [
            f"[source_seq={record.seq}] {record.as_line()}" for record in chunk
        ]

    def _fold_span(
        self, state: _SessionState, chunk: List[MemoryRecord]
    ) -> Optional[tuple[int, int, List[int]]]:
        """Resolve the current Trace surface range represented by ``chunk``."""
        seqs = [record.seq for record in chunk if record.seq is not None]
        if not seqs:
            return None
        from agentevolver.trace import trace_manager

        turns = {
            (record.agent_name, record.step)
            for record in chunk if record.step is not None
        }
        events = (
            trace_manager.events(state.session_id)
            if hasattr(trace_manager, "events") else []
        )
        by_seq = {
            event.seq_no: event for event in events
            if event.seq_no is not None
        }
        surface = (
            trace_manager.surface(state.session_id)
            if hasattr(trace_manager, "surface") else []
        )
        candidates = [
            seq for seq in surface
            if seq in by_seq and (
                seq in seqs or (
                    any(
                        record_step == by_seq[seq].step_number
                        and (not agent or agent == (by_seq[seq].agent_name or ""))
                        for agent, record_step in turns
                    )
                    and by_seq[seq].event_type in {
                        TraceEventType.AGENT_CALL,
                        TraceEventType.TOOL_CALL,
                        TraceEventType.SKILL_CALL,
                    }
                )
            )
        ]
        start, end = (
            (candidates[0], candidates[-1])
            if candidates else (min(seqs), max(seqs))
        )
        if (
            state.checkpoint_seq is not None
            and state.checkpoint_seq in surface
        ):
            start = state.checkpoint_seq
        shadowed = trace_manager.surface_span(state.session_id, start, end)
        if not shadowed:
            return None
        return start, end, shadowed

    async def _native_checkpoint(
        self, state: _SessionState, records: List[MemoryRecord]
    ) -> Optional[Dict[str, Any]]:
        """Ask a Responses model for one opaque checkpoint for this logical window."""
        try:
            span = self._fold_span(state, records)
            if span is None:
                return None
            _, end, _ = span
            from agentevolver.trace import trace_manager
            from agentevolver.trace.derive import derive_messages

            prefix = [
                event for event in trace_manager.events(state.session_id)
                if event.seq_no is not None and event.seq_no <= end
            ]
            messages = derive_messages(prefix)
            source = next(
                (event for event in reversed(prefix) if event.agent_name),
                None,
            )
            result = await model_manager.compact_history(
                self.model_name,
                messages,
                session_id=state.session_id,
                task_id=getattr(source, "task_id", None),
                agent_name=getattr(source, "agent_name", None),
                step_number=max(
                    (record.step for record in records if record.step is not None),
                    default=None,
                ),
            )
            if result:
                logger.info(
                    f"| 🗜️ {self.name}: installed native {result.get('provider')} "
                    f"compaction for {state.session_id}"
                )
            return result
        except Exception as error:  # transparent checkpoint remains authoritative fallback
            logger.warning(
                f"| ⚠️ {self.name}: native compaction unavailable; using text checkpoint "
                f"({error})"
            )
            return None

    async def _record_fold(
        self,
        state: _SessionState,
        chunk: list,
        summary: str,
        *,
        existing: str = "",
        native: Optional[Dict[str, Any]] = None,
    ) -> None:
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
        span = self._fold_span(state, chunk)
        if span is None:
            return
        start, end, shadowed = span
        # What the fold bought, carried on the fold itself. Stats, a training-sample
        # budget and the UI all want this number, and a consumer that derives it has to
        # hold the replaced range to subtract from — so each would keep its own copy of
        # what the summary shadowed, and they would disagree the first time one missed a
        # fold. Estimated with the same counter the request boundary uses, so the two
        # readings are in one unit.
        from agentevolver.model.pressure import estimate_tokens

        before = estimate_tokens([existing, *(r.as_line() for r in chunk)])
        after = estimate_tokens(summary)
        try:
            from agentevolver.trace import replace_op, trace_manager
            from agentevolver.trace.types import TraceEvent, TraceEventType

            fold_event = TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=state.session_id,
                label="compaction summary",
                message=summary,
                provider_state=(native or {}).get("provider_state") or {
                    "portable": {
                        "checkpoint": {"format": "portable.text.v1"}
                    }
                },
                usage=(native or {}).get("usage") or None,
                success=True,
                surface_op=replace_op(start, end),
                source_event_seqs=shadowed,
                metadata={
                    "type": "compaction",
                    "records": len(chunk),
                    "tokens_before": before,
                    "tokens_after": after,
                    # May be negative: a summary longer than the three short records it
                    # replaced is a real outcome, and recording it as a saving would make
                    # the total say compaction always helps.
                    "tokens_saved": before - after,
                    "checkpoint_format": (
                        (native or {}).get("format")
                        or ("native+text" if native else "portable.text.v1")
                    ),
                    "checkpoint_native": bool((native or {}).get("native")),
                    "native_model": (native or {}).get("model"),
                },
            )
            await trace_manager.emit(fold_event)
            state.checkpoint_seq = fold_event.seq_no
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
                file_path = str(path_manager.resolve_under(
                    self.base_dir, f"{stem}.{self.file_ext}",
                )) if self.base_dir else ""
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
