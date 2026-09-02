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
working_memory : bounded queue of portable text checkpoints. When the agent measures
                 high request pressure, native compaction is attempted only for an
                 explicitly capable model route. The ``compact`` hook supplies the
                 portable fallback/readable companion.
                 ``get()`` injects the last ``working_fetch`` summaries.

Portable summarisation is delegated to the ``compact`` hook (list[str] → text) so the
fallback logic lives in exactly one place.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from agentevolver.logger import logger
from agentevolver.memory.types import Memory
from agentevolver.model import model_manager
from agentevolver.paths import path_manager
from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.utils import assemble_workspace_path
from agentevolver.utils.file_utils import atomic_write_text

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
    atomic_write_text(file_path, content)


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

    @field_validator("ts", "event", "detail", "status", "agent_name", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        """Memory is textual even when a Trace error carries structured detail."""
        return _as_text(value)

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
    compact_hook: str = Field(
        default="compact",
        description="Hook used only for the portable text checkpoint fallback.",
    )

    recent_max: int = Field(default=30, description="Retention floor for direct/legacy compaction.")
    recent_fetch: int = Field(default=10, description="Recent records injected by get().")
    working_max: int = Field(default=10, description="Max working-memory summaries kept.")
    #: Per-entry inline detail cap. Oversized exact details remain in Trace and memory stores
    #: a source reference as a whole unit; it never splices a head and tail into a new fact.
    record_detail_max: int = Field(default=_RECORD_DETAIL_MAX,
                                   description="Max characters kept for one recent entry's detail.")
    working_fetch: int = Field(default=5, description="Working summaries injected by get().")
    compact_input_tokens: int = Field(
        default=60_000,
        description="Maximum estimated source tokens sent to one portable compaction call.",
    )
    compact_output_tokens: int = Field(
        default=2_048,
        description="Hard output ceiling for one portable checkpoint.",
    )

    persist_debounce: float = Field(default=0.2, description="Seconds to coalesce a burst of events into a single file write per session.")
    max_sessions: int = Field(default=256, description="Max in-memory sessions kept; least-recently-used ones are evicted beyond this.")

    file_ext: str = Field(default="txt", description="Persisted file extension.")

    def __init__(self, base_dir: str = "", **kwargs: Any) -> None:
        super().__init__(
            base_dir=str(assemble_workspace_path(base_dir)) if base_dir else "",
            **kwargs,
        )
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

        elif (
            ev == TraceEventType.CUSTOM
            and (event.metadata or {}).get("type") == "compaction"
        ):
            checkpoint = _as_text(event.message).strip()
            if checkpoint:
                state.working.clear()
                state.working.append(checkpoint)
                state.checkpoint_seq = event.seq_no
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
        # Never manufacture a head+tail hybrid and then let later turns mistake it for
        # the result. Trace is the exact source of truth; memory carries its locator.
        if (
            self.record_detail_max > 0
            and record.detail
            and len(record.detail) > self.record_detail_max
        ):
            record = record.model_copy(update={
                "detail": (
                    f"[Exact detail omitted inline as one complete unit: "
                    f"original_chars={len(record.detail):,}; "
                    f"source_seq={record.seq if record.seq is not None else 'unknown'}. "
                    "Retrieve the corresponding Trace event before relying on it.]"
                ),
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
        # tail. One pressure event performs at most one bounded summary call.
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
        """Atomically replace one closed-history window with one bounded checkpoint.

        The candidate is generated while the canonical records remain untouched.  Only
        a non-empty, bounded checkpoint that is smaller than the material it replaces is
        installed.  A provider error, cancellation, oversized answer, or concurrent
        history change therefore leaves both ``recent`` and ``working`` exactly as they
        were.  One pressure event can make at most one portable summariser call.
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
        outcome = "noop"
        chunks_done = 0
        # This run's own start time. It was read back out of `state.compaction` on every
        # chunk, which made a shared, deliberately-cleared field load-bearing for a fact
        # that never changes and belongs to this call.
        started_at = _ts()
        native_checkpoint: Optional[Dict[str, Any]] = None
        transaction_id: Optional[str] = None
        try:
            from agentevolver.hook import HookEvent, hook_manager
            from agentevolver.session import SessionContext

            await hook_manager.emit(
                HookEvent.PRE_COMPACT,
                {"memory": self.name, "recent_records": len(state.recent)},
                ctx=SessionContext(id=state.session_id),
            )
            state.compaction = {"started_at": started_at, "chunks": 0}
            await self._persist(state)

            records = list(state.recent)
            folding = records[: max(0, len(records) - floor)]
            if not folding:
                return
            transaction_id = uuid.uuid4().hex
            await self._record_compaction_transaction(
                state, transaction_id, "started", records=folding,
            )

            existing = state.working[-1] if state.working else ""
            native_checkpoint = await self._native_checkpoint(state, folding)
            text = str((native_checkpoint or {}).get("summary") or "").strip()
            source_items = self._summary_items(state, folding)
            portable_call = not bool(text)
            if not text:
                items = self._pack_summary_items(source_items)
                try:
                    text = await self._summarise(items, existing)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    outcome = "summary-error"
                    logger.warning(f"| ⚠️ {self.name}: summariser failed ({error})")
                    return

            valid, reason = self._valid_checkpoint(text, source_items, existing)
            if not valid:
                outcome = reason
                logger.warning(
                    f"| ⚠️ {self.name}: rejected compaction checkpoint ({reason})"
                )
                return

            # Commit only if the exact prefix we summarized is still present. Appends to
            # the live tail are fine; replacement or another fold is not.
            async with state._lock:
                current = list(state.recent)
                if len(current) < len(folding) or current[:len(folding)] != folding:
                    outcome = "history-changed"
                    return
                for _ in folding:
                    state.recent.popleft()
                state.working.clear()
                state.working.append(text)

            chunks_done = 1
            outcome = "ok"
            state.compaction = {"started_at": started_at, "chunks": 1}
            recorded = await self._record_fold(
                state, folding, text, existing=existing, native=native_checkpoint,
                source_items=source_items, portable_call=portable_call,
                transaction_id=transaction_id,
            )
            if recorded is False:
                # The append-only fold is the durable commit record. If it could not
                # enter Trace, restore the exact pre-commit memory projection and leave
                # the originals on Trace's unchanged surface.
                async with state._lock:
                    state.recent.extendleft(reversed(folding))
                    state.working.clear()
                    if existing:
                        state.working.append(existing)
                chunks_done = 0
                outcome = "trace-fold-rejected"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as error:
            outcome = "failed"
            logger.warning(f"| ⚠️ {self.name}: compaction failed ({error})")
        finally:
            if transaction_id:
                await self._record_compaction_transaction(
                    state, transaction_id,
                    "committed" if outcome == "ok" else "aborted",
                    outcome=outcome,
                )
            # Released last, and persisted with the release, so the bracket on disk
            # closes only once the work behind it is durable.
            state.compaction = None
            state._compacting = False
            try:
                await self._persist(state)
            except Exception as error:  # noqa: BLE001 — the compaction itself already happened
                logger.warning(f"| ⚠️ {self.name}: could not persist after compaction ({error})")
            try:
                from agentevolver.hook import HookEvent, hook_manager
                from agentevolver.session import SessionContext

                await hook_manager.emit(
                    HookEvent.POST_COMPACT,
                    {
                        "memory": self.name,
                        "outcome": outcome,
                        "chunks": chunks_done,
                    },
                    ctx=SessionContext(id=state.session_id),
                )
            except Exception as error:  # observational hook; compaction is already settled
                logger.warning(f"| ⚠️ Post-compaction hook failed: {error}")
            if outcome == "ok":
                logger.info(
                    f"| 🗜️ {self.name}: compacted {chunks_done} chunk(s) for {state.session_id}"
                )
            else:
                logger.info(
                    f"| 🗜️ {self.name}: compaction stopped ({outcome}) after {chunks_done} "
                    f"chunk(s) for {state.session_id}"
                )

    async def _record_compaction_transaction(
        self,
        state: _SessionState,
        transaction_id: str,
        phase: str,
        *,
        records: Optional[List[MemoryRecord]] = None,
        outcome: Optional[str] = None,
    ) -> None:
        """Durably bracket a fold so restart can distinguish rollback from commit."""
        try:
            from agentevolver.trace import trace_manager
            from agentevolver.trace.types import TraceEvent, TraceEventType

            accepted = await trace_manager.emit(TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=state.session_id,
                label=f"compaction {phase}",
                success=phase != "aborted",
                ignorable=True,
                source_event_seqs=[
                    int(record.seq) for record in (records or [])
                    if record.seq is not None
                ] or None,
                metadata={
                    "type": "compaction_transaction",
                    "transaction_id": transaction_id,
                    "phase": phase,
                    "outcome": outcome,
                },
            ))
            if phase == "started":
                running = bool(getattr(trace_manager, "running", False))
                if running and not accepted:
                    raise RuntimeError("compaction start marker was rejected by Trace")
                if running and accepted and not await trace_manager.flush():
                    raise RuntimeError("compaction start marker was not durably flushed")
        except Exception as error:
            if phase == "started":
                raise
            logger.warning(
                f"| ⚠️ {self.name}: could not record compaction {phase} ({error})"
            )

    def _pack_summary_items(self, items: List[str]) -> List[str]:
        """Bound a summary request without cutting through any source string."""
        if not items:
            return []
        # Four characters/token is intentionally conservative for code-heavy traces.
        budget = max(256, int(self.compact_input_tokens) * 4)
        joined = "\n".join(items)
        if len(joined) <= budget:
            return items
        notice = (
            f"[{len(items)} source items exceed this compaction input budget; complete "
            "unmodified sources remain in Trace. Recent whole items follow.]"
        )
        packed: List[str] = []
        used = len(notice)
        # Recent evidence is most likely to describe the current state. Retain only
        # complete items that fit; an individual oversized item is omitted as a whole.
        for item in reversed(items):
            cost = len(item) + 1
            if used + cost > budget:
                continue
            packed.append(item)
            used += cost
        packed.reverse()
        packed.insert(0, notice)
        # Keep the invariant local: callers cannot accidentally add an oversized
        # summarizer request if the allocation logic changes later.
        assert len("\n".join(packed)) <= budget
        return packed

    def _valid_checkpoint(
        self, text: str, source_items: List[str], existing: str,
    ) -> tuple[bool, str]:
        """Reject output expansion before it can replace canonical history."""
        from agentevolver.model.pressure import estimate_tokens

        if not text.strip():
            return False, "empty"
        output_tokens = estimate_tokens(text)
        if output_tokens > int(self.compact_output_tokens):
            return False, f"output-limit:{output_tokens}>{self.compact_output_tokens}"
        before = estimate_tokens([existing, *source_items])
        if output_tokens >= before:
            return False, f"no-token-saving:{before}->{output_tokens}"
        return True, "ok"

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

        def exact(value: Any) -> str:
            return _as_text(value)

        for event in events:
            seq = event.seq_no
            step = event.step_number
            if event.event_type == TraceEventType.AGENT_START and seq in direct:
                lines.append(
                    f"[source_seq={seq}] user task: "
                    f"{exact((event.input or {}).get('task', ''))}"
                )
            in_turn = any(
                record_step == step and (not agent or agent == (event.agent_name or ""))
                for agent, record_step in turns
            )
            if in_turn and event.event_type == TraceEventType.AGENT_CALL:
                visible = event.assistant_text or ""
                if visible.strip():
                    lines.append(
                        f"[source_seq={seq} step={step}] assistant: {exact(visible)}"
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
                    f"{exact(arguments)}"
                )
            elif in_turn and event.event_type in {
                TraceEventType.TOOL_CALL,
                TraceEventType.SKILL_CALL,
            }:
                result = event.message if event.success else (event.error or event.message)
                status = "ok" if event.success else "error"
                lines.append(
                    f"[source_seq={seq} step={step}] result {event.action_name} "
                    f"({status}): {exact(result)}"
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
        """Ask an explicitly capable route for one native checkpoint."""
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
                # Native providers count their own tokenizer while the request boundary
                # uses a conservative portable estimator. Leave headroom so a valid
                # provider summary cannot be rejected and regenerated on every step.
                max_output_tokens=max(256, int(self.compact_output_tokens * 0.75)),
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
        source_items: Optional[List[str]] = None,
        portable_call: bool = False,
        transaction_id: Optional[str] = None,
    ) -> Optional[bool]:
        """Tell the durable log that one summary now stands for a run of its events.

        Without this the two records of a session disagree after every compaction:
        memory's history has a summary where a dozen records used to be, and the trace
        log still has the dozen with nothing marking them as folded. Neither is wrong on
        its own and there is no way to line them up.

        The event replaces the range on the *surface* while the originals stay in the
        log untouched, so the folded records remain readable — the summary shadows them
        rather than deleting them, and cites every seq it shadowed.

        Returns ``False`` only when a running Trace rejected the fold. The caller then
        restores its pre-commit memory state. ``None`` means Trace was not active and the
        local memory-only mode remains available for isolated uses.
        """
        span = self._fold_span(state, chunk)
        if span is None:
            try:
                from agentevolver.trace import trace_manager
                return False if trace_manager.running else None
            except Exception:
                return None
        start, end, shadowed = span
        # What the fold bought, carried on the fold itself. Stats, a training-sample
        # budget and the UI all want this number, and a consumer that derives it has to
        # hold the replaced range to subtract from — so each would keep its own copy of
        # what the summary shadowed, and they would disagree the first time one missed a
        # fold. Estimated with the same counter the request boundary uses, so the two
        # readings are in one unit.
        from agentevolver.model.pressure import estimate_tokens

        before = estimate_tokens([existing, *(source_items or [r.as_line() for r in chunk])])
        after = estimate_tokens(summary)
        from agentevolver.memory.checkpoint import PortableCheckpoint

        checkpoint = PortableCheckpoint.from_text(summary)
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
                    "transaction_id": transaction_id,
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
                    "model_calls": 0 if (native or {}).get("summary") else int(portable_call),
                    "source_characters": sum(
                        len(item) for item in (source_items or [])
                    ),
                    "checkpoint_characters": len(summary),
                    "checkpoint_schema_version": checkpoint.schema_version,
                    "checkpoint": checkpoint.model_dump(mode="json"),
                    "savings_ratio": (
                        (before - after) / before if before else 0.0
                    ),
                },
            )
            accepted = await trace_manager.emit(fold_event)
            if accepted:
                state.checkpoint_seq = fold_event.seq_no
            return accepted
        except Exception as error:  # noqa: BLE001 — caller restores the candidate
            logger.warning(f"| ⚠️ {self.name}: could not record the fold in the trace ({error})")
            return False

    async def _summarise(self, items: list[str], existing: str) -> str:
        from agentevolver.hook import HookEvent, hook_manager
        res = await hook_manager(name=self.compact_hook, input={
            "event": HookEvent.DIRECT_CALL, "items": items,
            "existing_summary": existing, "model_name": self.model_name,
            "max_output_tokens": self.compact_output_tokens,
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
        created = False
        async with self._registry_lock:
            if session_id not in self._sessions:
                created = True
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
        if created:
            await self._rehydrate_state(state, current_event=event)
        return state

    async def _rehydrate_state(
        self, state: _SessionState, *, current_event: TraceEvent,
    ) -> None:
        """Rebuild the memory projection from Trace's authoritative surface."""
        try:
            from agentevolver.trace import trace_manager

            events = trace_manager.rehydrate(state.session_id)
            surface = set(trace_manager.surface(state.session_id))
        except Exception as error:
            # Existing durable history that cannot be interpreted is not equivalent to
            # an empty session. Fail closed rather than quietly forgetting it.
            from agentevolver.trace import trace_manager

            if trace_manager.read_from(state.session_id, durable=True):
                raise RuntimeError(
                    f"cannot restore memory for {state.session_id}: {error}"
                ) from error
            return

        checkpoints = [
            candidate for candidate in events
            if candidate.seq_no in surface
            and candidate.event_type == TraceEventType.CUSTOM
            and (candidate.metadata or {}).get("type") == "compaction"
        ]
        boundary = max(
            (
                max(candidate.source_event_seqs or [-1])
                for candidate in checkpoints
            ),
            default=-1,
        )
        replay = [
            candidate for candidate in events
            if candidate.id != current_event.id and (
                candidate.seq_no in surface
                or (
                    candidate.event_type in {
                        TraceEventType.TOOL_START, TraceEventType.SKILL_START,
                    }
                    and int(candidate.seq_no or -1) > boundary
                )
            )
        ]
        for candidate in replay:
            await self.emit(candidate, state.session_id)
        transactions: Dict[str, set[str]] = {}
        for candidate in events:
            metadata = candidate.metadata or {}
            if metadata.get("type") != "compaction_transaction":
                continue
            transaction = str(metadata.get("transaction_id") or "")
            if transaction:
                transactions.setdefault(transaction, set()).add(
                    str(metadata.get("phase") or "")
                )
        folded_transactions = {
            str((candidate.metadata or {}).get("transaction_id") or "")
            for candidate in events
            if (candidate.metadata or {}).get("type") == "compaction"
        }
        interrupted = [
            transaction for transaction, phases in transactions.items()
            if "started" in phases and not ({"committed", "aborted"} & phases)
            and transaction not in folded_transactions
        ]
        if interrupted:
            self._append_recent(state, MemoryRecord(
                ts=_ts(),
                event="Interrupted compaction recovered",
                detail=(
                    "Trace contained an unclosed compaction transaction; canonical "
                    "events were retained and the candidate checkpoint was rolled back."
                ),
                status="done",
            ))
        if replay:
            logger.info(
                f"| 🔄 {self.name}: restored {len(replay)} event(s) for "
                f"{state.session_id} from Trace"
            )

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
