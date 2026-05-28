"""FileSystemMemory — per-session execution state persisted as a self-contained HTML file.

Interface (mirrors GeneralMemorySystem):
    await mem.emit(event, session_id)   # ingest a TraceEvent; never blocks caller
    html  = await mem.get(session_id)   # full HTML string for the session

HTML Sections
-------------
  Task             ← AGENT_START  (input["task"])
  TodoList         ← event-driven:
                       sub-agents : POST_STEP flush of _pending_step_actions (same data as flow chart)
                       MetaAgent  : AGENT_CALL (metadata["subtask_event"]["action"] == "subtask_planned")
                       legacy     : AGENT_CALL (metadata["todos"]) — explicit snapshot, kept as fallback
  FlowChart        ← event-driven (same sources as TodoList)
                       legacy     : AGENT_CALL (metadata["flow_steps"])
  ExecutionHistory ← AGENT_START/END, TOOL_CALL, SKILL_CALL, AGENT_CALL(note), ERROR
  FinalResult      ← AGENT_CALL   (metadata["final_result"])

Concurrency
-----------
* emit() updates in-memory state synchronously, then spawns a background save task.
* Per-session asyncio.Lock ensures HTML writes are serialised even under concurrent emits.
* Long todo descriptions are summarised by an LLM in a background task; the file is
  saved once summarisation finishes (not before, to avoid a half-cooked write).
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import Field

from src.logger import logger
from src.memory.types import Memory
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager
from src.registry import MEMORY_SYSTEM
from src.trace.types import TraceEvent, TraceEventType
from src.utils import assemble_project_path
from src.visual import css_path


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _he(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_FLOW_LABEL_MAX = 80   # max chars for a flow-chart node label

# Maps MetaAgent subtask lifecycle event names → display status strings.
_SUBTASK_STATUS_MAP: Dict[str, str] = {
    "subtask_dispatch":  "running",
    "subtask_done":      "done",
    "subtask_failed":    "failed",
    "subtask_cancelled": "cancelled",
}

_NODE_CSS = {
    "done":      "node-done",
    "running":   "node-running",
    "failed":    "node-failed",
    "pending":   "node-pending",
    "skipped":   "node-skipped",
    "cancelled": "node-skipped",
}
_BADGE_CSS = {
    "done":      "status-done",
    "running":   "status-running",
    "failed":    "status-failed",
    "pending":   "status-pending",
    "skipped":   "status-skipped",
    "cancelled": "status-cancelled",
}


def _badge(status: str) -> str:
    css = _BADGE_CSS.get(status, "status-pending")
    return (
        f'<span class="status-badge {css}">'
        f'<span class="status-dot"></span>'
        f'{_he(status)}</span>'
    )


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

class _TodoEntry:
    __slots__ = ("id", "description", "agent_name", "status")

    def __init__(self, id: str, description: str, agent_name: str = "", status: str = "pending") -> None:
        self.id = id
        self.description = description
        self.agent_name = agent_name
        self.status = status


class _FlowStep:
    __slots__ = ("step", "label", "agents", "status", "round", "round_label")

    def __init__(self, step: int, label: str, agents: List[str],
                 status: str, round: int, round_label: str = "") -> None:
        self.step = step
        self.label = label
        self.agents = agents
        self.status = status
        self.round = round
        self.round_label = round_label


class _HistoryEntry:
    __slots__ = ("ts", "event", "detail", "status")

    def __init__(self, ts: str, event: str, detail: str = "", status: str = "") -> None:
        self.ts = ts
        self.event = event
        self.detail = detail
        self.status = status


# ---------------------------------------------------------------------------
# Per-session state + HTML renderer
# ---------------------------------------------------------------------------

class _SessionState:
    def __init__(self, session_id: str, task: str, file_path: str) -> None:
        self.session_id = session_id
        self.task = task
        self.file_path = file_path

        self.todos: List[_TodoEntry] = []
        self.flow_steps: List[_FlowStep] = []
        self.history: List[_HistoryEntry] = []
        self.history_summary: str = ""   # LLM-compressed summary of overflow execution events
        self.compact_summary: str = ""   # LLM summary of compacted conversation messages (from CompactHook)
        self.final_result: Optional[str] = None
        self.result_success: bool = True

        # Pending tool/skill actions buffered per step_number until POST_STEP arrives.
        # Key: step_number, Value: list of action dicts to be flushed into flow_steps.
        self._pending_step_actions: Dict[int, List[Dict[str, Any]]] = {}

        # Latest agent-written memory notes (from AgentThinkOutput.memory field).
        self.agent_memory: str = ""

        # Index for MetaAgent subtask events: subtask_id → list index in todos / flow_steps.
        # Enables O(1) status updates without scanning the whole list.
        self._subtask_todo_index: Dict[str, int] = {}
        self._subtask_flow_index: Dict[str, int] = {}

        # Serialises concurrent HTML writes for this session
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._todos_lock: asyncio.Lock = asyncio.Lock()  # serialises _apply_todos calls
        self._compressing: bool = False  # guard against concurrent compression tasks

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def render(self) -> str:
        css_rel = os.path.relpath(css_path("memory.css"), start=os.path.dirname(self.file_path))
        parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            f'  <title>Memory — {_he(self.session_id)}</title>',
            f'  <link rel="stylesheet" href="{_he(css_rel)}">',
            "</head>",
            "<body>",
            '<div class="memory-page">',
            self._render_header(),
            self._render_todos(),
            self._render_flowchart(),
            self._render_history(),
        ]
        if self.final_result is not None:
            parts.append(self._render_result())
        parts += ["</div>", "</body>", "</html>"]
        return "\n".join(parts)

    def _render_header(self) -> str:
        return (
            '<div class="mem-header">'
            '<div class="mem-label">Memory</div>'
            f'<p class="mem-task">{_he(self.task)}</p>'
            f'<code class="mem-session">{_he(self.session_id)}</code>'
            "</div>"
        )

    def _render_todos(self) -> str:
        lines = ['<div class="mem-section">', "<h2>Todo List</h2>"]
        if not self.todos:
            lines += ['<p class="mem-empty">No tasks yet.</p>', "</div>"]
            return "\n".join(lines)

        lines += [
            '<table class="todo-table">',
            "<thead><tr>"
            "<th>#</th><th>Agent</th><th>Status</th><th>Description</th>"
            "</tr></thead><tbody>",
        ]
        for i, t in enumerate(self.todos, 1):
            agent_cell = (
                f'<code class="todo-agent">{_he(t.agent_name)}</code>'
                if t.agent_name else '<span class="mem-empty">—</span>'
            )
            lines.append(
                f"<tr>"
                f'<td class="todo-num">{i}</td>'
                f"<td>{agent_cell}</td>"
                f"<td>{_badge(t.status)}</td>"
                f'<td class="todo-desc">{_he(t.description)}</td>'
                "</tr>"
            )
        lines += ["</tbody>", "</table>", "</div>"]
        return "\n".join(lines)

    def _render_flowchart(self) -> str:
        lines = ['<div class="mem-section">', "<h2>Flow Chart</h2>"]
        if not self.flow_steps:
            lines += ['<p class="mem-empty">No flow steps yet.</p>', "</div>"]
            return "\n".join(lines)

        by_round: Dict[int, List[_FlowStep]] = defaultdict(list)
        for s in self.flow_steps:
            by_round[s.round].append(s)

        lines.append('<div class="flow-chart">')
        first = True
        for rnum in sorted(by_round.keys()):
            if not first:
                lines.append('<div class="flow-connector"></div>')
            first = False

            steps = by_round[rnum]
            # Round header: show round number + goal label
            round_label = steps[0].round_label if steps and steps[0].round_label else f"Round {rnum}"
            lines.append(
                f'<div class="flow-round-header">'
                f'<span class="flow-round-num">Round {rnum}</span>'
                f'<span class="flow-round-goal">{_he(round_label)}</span>'
                f'</div>'
            )

            lines.append('<div class="flow-round">')
            for s in steps:
                node_css = _NODE_CSS.get(s.status, "node-pending")
                agents_html = " ".join(f'<code>{_he(a)}</code>' for a in s.agents) if s.agents else ""
                lines.append(
                    f'<div class="flow-node {node_css}">'
                    f'<span class="flow-step-num">{s.step}</span>'
                    '<div class="flow-step-body">'
                    + (f'<div class="flow-step-agent">{agents_html}</div>' if agents_html else "")
                    + f'<div class="flow-step-label">{_he(s.label)}</div>'
                    '</div>'
                    + _badge(s.status)
                    + '</div>'
                )
            lines.append("</div>")  # flow-round

        lines += ["</div>", "</div>"]  # flow-chart, mem-section
        return "\n".join(lines)

    def _render_history(self) -> str:
        lines = ['<div class="mem-section">', "<h2>Execution History</h2>"]
        if not self.history:
            lines += ['<p class="mem-empty">No history yet.</p>', "</div>"]
            return "\n".join(lines)

        if self.compact_summary:
            lines.append(
                '<details class="compact-summary">'
                '<summary>Compacted conversation history</summary>'
                f'<pre class="compact-summary-body">{_he(self.compact_summary)}</pre>'
                '</details>'
            )

        lines.append('<div class="history-timeline">')
        for e in self.history:
            badge_html = f" {_badge(e.status)}" if e.status else ""
            detail_html = (
                f'<div class="history-detail">{_he(e.detail)}</div>' if e.detail else ""
            )
            lines.append(
                f'<div class="history-entry">'
                f'<span class="history-ts">{_he(e.ts)}</span>'
                f'<span class="history-dot"></span>'
                f'<div class="history-body">'
                f'<div class="history-event">{_he(e.event)}{badge_html}</div>'
                + detail_html
                + "</div></div>"
            )
        lines += ["</div>", "</div>"]
        return "\n".join(lines)

    def _render_result(self) -> str:
        extra = "" if self.result_success else " result-failed"
        tag_cls = "tag-success" if self.result_success else "tag-failed"
        tag_label = "Completed" if self.result_success else "Failed"
        return (
            f'<div class="mem-section result-section{extra}">'
            "<h2>Final Result</h2>"
            f'<span class="result-tag {tag_cls}">{tag_label}</span>'
            f"<pre>{_he(self.final_result)}</pre>"
            "</div>"
        )


# ---------------------------------------------------------------------------
# FileSystemMemory
# ---------------------------------------------------------------------------

# (unused — kept for external introspection)
_HISTORY_TYPES = frozenset({
    TraceEventType.AGENT_START,
    TraceEventType.AGENT_CALL,
    TraceEventType.AGENT_END,
    TraceEventType.TOOL_CALL,
    TraceEventType.SKILL_CALL,
    TraceEventType.ERROR,
})


@MEMORY_SYSTEM.register_module(force=True)
class FileSystemMemory(Memory):
    """File-system backed memory that persists session state as a self-contained HTML file."""

    name: str = Field(default="file_system_memory")
    description: str = Field(
        default="Persists task, todos, flowchart, history and result to an HTML file."
    )
    base_dir: str = Field(default="")
    model_name: str = Field(default="openrouter/gemini-3-flash-preview")
    max_todo_length: int = Field(default=80)
    max_history: int = Field(default=40, description="Trigger history compression when entry count exceeds this.")
    keep_recent: int = Field(default=20, description="Number of recent history entries to keep verbatim after compression.")

    def __init__(
        self,
        base_dir: str,
        model_name: str = "openrouter/gemini-3-flash-preview",
        max_todo_length: int = 80,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_dir=str(assemble_project_path(base_dir)),
            model_name=model_name,
            max_todo_length=max_todo_length,
            **kwargs,
        )
        os.makedirs(self.base_dir, exist_ok=True)
        self._sessions: Dict[str, _SessionState] = {}
        self._registry_lock = asyncio.Lock()   # guards self._sessions dict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _append_history(self, state: _SessionState, entry: _HistoryEntry) -> None:
        """Append a history entry and fire compression if threshold exceeded."""
        state.history.append(entry)
        if len(state.history) > self.max_history and not state._compressing:
            asyncio.create_task(self._compress_history(state))

    async def emit(self, event: TraceEvent, session_id: str) -> None:
        """Ingest a TraceEvent. Never blocks the caller."""
        state = await self._get_or_create(session_id, event)

        ev = event.event_type

        if ev == TraceEventType.AGENT_START:
            # Task is already set in _get_or_create; just record history
            task_desc = (event.input or {}).get("task", "")
            self._append_history(state, _HistoryEntry(
                ts=_ts(),
                event=f"Agent started: {event.agent_name or ''}",
                detail=task_desc or "",
                status="running",
            ))
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.AGENT_END:
            success = event.metadata.get("success", not bool(event.error))
            result = event.output if isinstance(event.output, str) else str(event.output or "")
            error = event.error or ""
            self._append_history(state, _HistoryEntry(
                ts=_ts(),
                event=f"Agent ended: {event.agent_name or ''}",
                detail=error if not success else result,
                status="done" if success else "failed",
            ))
            # Only set final result when the root agent (no parent) ends
            if event.metadata.get("is_root", False):
                state.final_result = error if not success else result
                state.result_success = success
            asyncio.create_task(self._save(state))

        elif ev in (TraceEventType.TOOL_CALL, TraceEventType.SKILL_CALL):
            ok = event.metadata.get("success", not bool(event.error))
            detail = event.error if not ok else ""
            if isinstance(event.output, str):
                detail = detail or event.output
            self._append_history(state, _HistoryEntry(
                ts=_ts(),
                event=f"{event.action_name or event.action_type or 'action'} result",
                detail=detail,
                status="done" if ok else "failed",
            ))
            # Buffer for flow chart — flushed into flow_steps when POST_STEP AGENT_CALL arrives.
            if event.step_number is not None:
                buf = state._pending_step_actions.setdefault(event.step_number, [])
                buf.append({
                    "action_name": event.action_name or event.action_type or "action",
                    "description": event.metadata.get("description") or event.action_name or "",
                    "action_type": event.action_type or "tool",
                    "success": ok,
                    "action_index": event.action_index or 0,
                })
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.ERROR:
            self._append_history(state, _HistoryEntry(
                ts=_ts(),
                event="Error",
                detail=event.error or str(event.output or ""),
                status="failed",
            ))
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.AGENT_CALL:
            md = event.metadata
            changed = False

            # ── POST_STEP variant (from MemoryHook POST_STEP → agent_call_event) ──
            # Identified by: step_number set + output dict containing "next_goal".
            # Flush buffered tool/skill actions for this step into flow_steps AND todos.
            ev_output = event.output if isinstance(event.output, dict) else {}
            if event.step_number is not None and "next_goal" in ev_output:
                step = event.step_number
                next_goal = (ev_output.get("next_goal") or f"Step {step}")[:_FLOW_LABEL_MAX]
                agent_mem = ev_output.get("memory") or ""
                if agent_mem:
                    state.agent_memory = agent_mem
                pending = state._pending_step_actions.pop(step, [])
                if pending:
                    pending.sort(key=lambda a: a["action_index"])
                    for act in pending:
                        label = (act["description"] or act["action_name"])[:_FLOW_LABEL_MAX]
                        act_status = "done" if act["success"] else "failed"
                        # Flow chart node
                        state.flow_steps.append(_FlowStep(
                            step=len(state.flow_steps) + 1,
                            label=label,
                            agents=[act["action_name"]],
                            status=act_status,
                            round=step,
                            round_label=next_goal,
                        ))
                        # Matching todo row (same data, tabular view)
                        state.todos.append(_TodoEntry(
                            id=f"step{step}-a{act['action_index']}",
                            description=label,
                            agent_name=act["action_name"],
                            status=act_status,
                        ))
                    changed = True

            # ── subtask_event variant (MetaAgent incremental todo/flow updates) ──
            # MetaAgent emits structured subtask lifecycle events so FileSystemMemory
            # can maintain todos and flow_steps without a full snapshot push.
            if "subtask_event" in md:
                se = md["subtask_event"]
                se_action = se.get("action", "")
                se_data = se.get("data", {})
                subtask_id = se_data.get("subtask_id", "")

                if se_action == "subtask_planned":
                    task_desc = se_data.get("task", "")[:_FLOW_LABEL_MAX]
                    agent_name = se_data.get("agent_name", "")
                    round_no = se_data.get("round", 1)
                    round_label = se_data.get("round_label", f"Round {round_no}")
                    # Add todo row
                    state._subtask_todo_index[subtask_id] = len(state.todos)
                    state.todos.append(_TodoEntry(
                        id=subtask_id,
                        description=task_desc,
                        agent_name=agent_name,
                        status="pending",
                    ))
                    # Add flow node
                    state._subtask_flow_index[subtask_id] = len(state.flow_steps)
                    state.flow_steps.append(_FlowStep(
                        step=len(state.flow_steps) + 1,
                        label=task_desc,
                        agents=[agent_name],
                        status="pending",
                        round=round_no,
                        round_label=round_label,
                    ))
                    changed = True

                elif se_action in _SUBTASK_STATUS_MAP:
                    new_status = _SUBTASK_STATUS_MAP[se_action]
                    idx = state._subtask_todo_index.get(subtask_id)
                    if idx is not None and idx < len(state.todos):
                        state.todos[idx].status = new_status
                    idx = state._subtask_flow_index.get(subtask_id)
                    if idx is not None and idx < len(state.flow_steps):
                        state.flow_steps[idx].status = new_status
                    changed = True

            # ── ON_CALL explicit payload (legacy / external override) ──
            if "todos" in md:
                asyncio.create_task(self._apply_todos(state, md["todos"]))

            if "flow_steps" in md:
                state.flow_steps = [
                    _FlowStep(
                        step=s.get("step", i + 1),
                        label=s.get("label", "")[:_FLOW_LABEL_MAX],
                        agents=s.get("agents", []),
                        status=s.get("status", "pending"),
                        round=s.get("round", 1),
                        round_label=s.get("round_label", ""),
                    )
                    for i, s in enumerate(md["flow_steps"])
                ]
                changed = True

            if "note" in md:
                n = md["note"]
                self._append_history(state, _HistoryEntry(
                    ts=_ts(),
                    event=n.get("event", event.label),
                    detail=n.get("detail", ""),
                    status=n.get("status", ""),
                ))
                changed = True

            if "compact_summary" in md:
                summary = md.get("compact_summary", "")
                covers_steps = md.get("covers_steps", 0)
                if summary:
                    state.compact_summary = summary
                self._append_history(state, _HistoryEntry(
                    ts=_ts(),
                    event=f"Context compacted (steps 1–{covers_steps})",
                    detail="Conversation history compressed to reduce token usage.",
                    status="done",
                ))
                changed = True

            if "final_result" in md:
                state.final_result = md["final_result"]
                state.result_success = md.get("success", True)
                changed = True

            if changed:
                asyncio.create_task(self._save(state))

    async def get(self, session_id: str, short_term_n: int = 10, **kwargs) -> Optional[str]:
        """Return a text summary of the session memory for prompt injection."""
        async with self._registry_lock:
            state = self._sessions.get(session_id)
        if state is None:
            return None

        lines: list[str] = []

        if state.agent_memory:
            lines.append("## Agent Memory")
            lines.append(state.agent_memory)
            lines.append("")

        if state.compact_summary:
            lines.append("## Summary")
            lines.append(state.compact_summary)
            lines.append("")

        recent = state.history[-short_term_n:] if short_term_n else state.history
        if recent:
            lines.append("## Recent History")
            for e in recent:
                status = f" [{e.status}]" if e.status else ""
                detail = f": {e.detail}" if e.detail else ""
                lines.append(f"[{e.ts}] {e.event}{status}{detail}")

        result = "\n".join(lines).strip()
        return result or None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_or_create(self, session_id: str, event: TraceEvent) -> _SessionState:
        async with self._registry_lock:
            if session_id not in self._sessions:
                task = ""
                if event.event_type == TraceEventType.AGENT_START:
                    task = (event.input or {}).get("task", "")
                os.makedirs(self.base_dir, exist_ok=True)
                file_path = os.path.join(self.base_dir, f"{session_id}.memory.html")
                state = _SessionState(session_id=session_id, task=task, file_path=file_path)
                self._sessions[session_id] = state
                logger.info(f"| 📄 FileSystemMemory: created session {session_id} → {file_path}")
            return self._sessions[session_id]

    async def _compress_history(self, state: _SessionState) -> None:
        """LLM-compress overflow history entries into state.history_summary."""
        state._compressing = True
        try:
            overflow = state.history[:-self.keep_recent]
            state.history = state.history[-self.keep_recent:]

            entries_text = "\n".join(
                f"[{e.ts}] {e.event}{' [' + e.status + ']' if e.status else ''}"
                f"{': ' + e.detail if e.detail else ''}"
                for e in overflow
            )
            existing = (
                f"Existing summary:\n{state.history_summary}\n\nNew entries to incorporate:\n"
                if state.history_summary else ""
            )
            prompt = (
                f"{existing}{entries_text}\n\n"
                "Summarize the above execution history in 2-4 sentences. "
                "Focus on what agents ran, what tools were called, and what succeeded or failed."
            )
            response = await model_manager(
                name=self.model_name,
                input={
                    "messages": [
                        SystemMessage(content="You are a concise execution history summarizer."),
                        HumanMessage(content=prompt),
                    ],
                },
            )
            state.history_summary = response.message.strip()
            await self._save(state)
            logger.info(f"| 🗜️ FileSystemMemory: compressed {len(overflow)} history entries for {state.session_id}")
        except Exception as e:
            logger.warning(f"| ⚠️ FileSystemMemory: history compression failed ({e})")
        finally:
            state._compressing = False

    async def _apply_todos(self, state: _SessionState, raw_todos: List[Dict[str, Any]]) -> None:
        """Summarise long todo descriptions, update state, then save.

        Serialised per session via _todos_lock so that rapid consecutive
        _sync_state calls don't race to overwrite state.todos with stale data.
        """
        async with state._todos_lock:
            entries: List[_TodoEntry] = []
            tasks = [self._maybe_summarize(t.get("description", "")) for t in raw_todos]
            summaries = await asyncio.gather(*tasks, return_exceptions=True)

            for t, summary in zip(raw_todos, summaries):
                desc = summary if isinstance(summary, str) else t.get("description", "")
                entries.append(_TodoEntry(
                    id=t.get("id", ""),
                    description=desc,
                    agent_name=t.get("agent_name", ""),
                    status=t.get("status", "pending"),
                ))
            state.todos = entries
            await self._save(state)

    async def _maybe_summarize(self, description: str) -> str:
        if len(description) <= self.max_todo_length:
            return description
        try:
            response = await model_manager(
                name=self.model_name,
                input={
                    "messages": [
                        SystemMessage(content="You are a concise summariser."),
                        HumanMessage(content=(
                            f"Summarise this task description in at most {self.max_todo_length} characters. "
                            f"Return ONLY the summary, no extra text.\n\n{description}"
                        )),
                    ],
                },
            )
            return response.message.strip()[: self.max_todo_length]
        except Exception as e:
            logger.warning(f"| ⚠️ FileSystemMemory: summarisation failed ({e}), truncating")
            return description[: self.max_todo_length]

    async def _save(self, state: _SessionState) -> None:
        """Render HTML and write to disk; serialised per session via _write_lock.

        render() is called inside the lock so the snapshot written to disk always
        reflects the state at the moment the lock is held — not an earlier render
        that raced with a concurrent state mutation.
        """
        async with state._write_lock:
            html = state.render()
            await asyncio.to_thread(_write_sync, state.file_path, html)


# ---------------------------------------------------------------------------
# Sync I/O helpers (run in thread pool)
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _write_sync(file_path: str, content: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _read_sync(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as fh:
        return fh.read()
