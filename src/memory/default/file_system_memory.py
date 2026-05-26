"""FileSystemMemory — per-session execution state persisted as a self-contained HTML file.

Interface (mirrors GeneralMemorySystem):
    await mem.emit(event, session_id)   # ingest a TraceEvent; never blocks caller
    html  = await mem.get(session_id)   # full HTML string for the session

HTML Sections
-------------
  Task             ← first AGENT_START  (input["task"])
  TodoList         ← CUSTOM  metadata["type"]="todo_update"
                              metadata["todos"]=[{id, description, agent_name, status}, ...]
  FlowChart        ← CUSTOM  metadata["type"]="flowchart_update"
                              metadata["steps"]=[{step, label, agents, status, round}, ...]
  ExecutionHistory ← AGENT_START/END, TOOL_RESULT, SKILL_RESULT, ERROR
  FinalResult      ← AGENT_END  (metadata["success"], output)

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
    __slots__ = ("step", "label", "agents", "status", "round")

    def __init__(self, step: int, label: str, agents: List[str],
                 status: str, round: int) -> None:
        self.step = step
        self.label = label
        self.agents = agents
        self.status = status
        self.round = round


class _PlanEntry:
    __slots__ = ("id", "description", "status")

    def __init__(self, id: str, description: str, status: str = "pending") -> None:
        self.id = id
        self.description = description
        self.status = status


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
        self.plan: List[_PlanEntry] = []
        self.history: List[_HistoryEntry] = []
        self.history_summary: str = ""   # LLM-compressed summary of overflow execution events
        self.compact_summary: str = ""   # LLM summary of compacted conversation messages (from CompactHook)
        self.final_result: Optional[str] = None
        self.result_success: bool = True

        # Serialises concurrent HTML writes for this session
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._compressing: bool = False  # guard against concurrent compression tasks

    # ------------------------------------------------------------------
    # Text summary (for prompt injection)
    # ------------------------------------------------------------------

    def to_text(self, recent_n: int = 20) -> str:
        """Return a markdown text summary of session state, suitable for prompt injection."""
        parts: List[str] = []

        if self.task:
            parts.append(f"**Task:** {self.task}")

        if self.plan:
            _plan_icon = {"pending": "☐", "in_progress": "▶", "done": "✓", "failed": "✗"}
            parts.append("\n**Execution Plan:**")
            for p in self.plan:
                icon = _plan_icon.get(p.status, "☐")
                parts.append(f"  {icon} [{p.id}] {p.description}")

        if self.todos:
            parts.append("\n**Todo List:**")
            for t in self.todos:
                mark = "✓" if t.status == "done" else ("✗" if t.status == "failed" else "○")
                agent = f" [{t.agent_name}]" if t.agent_name else ""
                parts.append(f"  {mark} [{t.status}] {t.description}{agent}")

        if self.flow_steps:
            parts.append("\n**Execution Plan:**")
            for s in self.flow_steps:
                agents = f" ({', '.join(s.agents)})" if s.agents else ""
                parts.append(f"  Step {s.step}: [{s.status}] {s.label}{agents}")

        if self.compact_summary:
            parts.append(f"\n**Compacted Conversation History:**\n{self.compact_summary}")

        if self.history_summary:
            parts.append(f"\n**Earlier Execution History (summarized):**\n  {self.history_summary}")

        recent = self.history[-recent_n:] if recent_n else self.history
        if recent:
            parts.append("\n**Recent History:**")
            for h in recent:
                detail = f": {h.detail}" if h.detail else ""
                status = f" [{h.status}]" if h.status else ""
                parts.append(f"  [{h.ts}] {h.event}{status}{detail}")

        if self.final_result is not None:
            parts.append(f"\n**Result:** {self.final_result}")

        return "\n".join(parts)

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
            self._render_plan(),
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

    def _render_plan(self) -> str:
        lines = ['<div class="mem-section">', "<h2>Execution Plan</h2>"]
        if not self.plan:
            lines += ['<p class="mem-empty">No plan yet.</p>', "</div>"]
            return "\n".join(lines)

        lines += [
            '<table class="todo-table">',
            "<thead><tr><th>#</th><th>ID</th><th>Status</th><th>Description</th></tr></thead><tbody>",
        ]
        for i, p in enumerate(self.plan, 1):
            lines.append(
                f"<tr>"
                f'<td class="todo-num">{i}</td>'
                f'<td><code class="todo-agent">{_he(p.id)}</code></td>'
                f"<td>{_badge(p.status)}</td>"
                f'<td class="todo-desc">{_he(p.description)}</td>'
                "</tr>"
            )
        lines += ["</tbody>", "</table>", "</div>"]
        return "\n".join(lines)

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

            lines.append('<div class="flow-round">')
            for s in by_round[rnum]:
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

# Event types that get written to ExecutionHistory
_HISTORY_TYPES = frozenset({
    TraceEventType.AGENT_START,
    TraceEventType.AGENT_END,
    TraceEventType.TOOL_RESULT,
    TraceEventType.SKILL_RESULT,
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
                detail=task_desc[:200] if task_desc else "",
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
                detail=error if not success else result[:200],
                status="done" if success else "failed",
            ))
            # Only set final result when the root agent (no parent) ends
            if event.metadata.get("is_root", False):
                state.final_result = error if not success else result
                state.result_success = success
            asyncio.create_task(self._save(state))

        elif ev in (TraceEventType.TOOL_RESULT, TraceEventType.SKILL_RESULT):
            ok = event.metadata.get("success", not bool(event.error))
            detail = event.error if not ok else ""
            if isinstance(event.output, str):
                detail = detail or event.output[:200]
            self._append_history(state, _HistoryEntry(
                ts=_ts(),
                event=f"{event.action_name or event.action_type or 'action'} result",
                detail=detail,
                status="done" if ok else "failed",
            ))
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.ERROR:
            self._append_history(state, _HistoryEntry(
                ts=_ts(),
                event="Error",
                detail=event.error or str(event.output or ""),
                status="failed",
            ))
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.PLAN_INIT:
            state.plan = [
                _PlanEntry(id=i["id"], description=i["description"], status=i.get("status", "pending"))
                for i in event.metadata.get("items", [])
            ]
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.PLAN_UPDATE:
            update_map = {u["id"]: u["status"] for u in event.metadata.get("updates", [])}
            for p in state.plan:
                if p.id in update_map:
                    p.status = update_map[p.id]
            asyncio.create_task(self._save(state))

        elif ev == TraceEventType.CUSTOM:
            meta_type = event.metadata.get("type")

            if meta_type == "todo_update":
                # Summarise long descriptions in background, then save once.
                raw_todos = event.metadata.get("todos", [])
                asyncio.create_task(self._apply_todos(state, raw_todos))

            elif meta_type == "flowchart_update":
                raw_steps = event.metadata.get("steps", [])
                state.flow_steps = [
                    _FlowStep(
                        step=s.get("step", i + 1),
                        label=s.get("label", ""),
                        agents=s.get("agents", []),
                        status=s.get("status", "pending"),
                        round=s.get("round", 1),
                    )
                    for i, s in enumerate(raw_steps)
                ]
                asyncio.create_task(self._save(state))

            elif meta_type == "final_result":
                state.final_result = event.metadata.get("result", str(event.output or ""))
                state.result_success = event.metadata.get("success", True)
                asyncio.create_task(self._save(state))

            elif meta_type == "history_entry":
                self._append_history(state, _HistoryEntry(
                    ts=_ts(),
                    event=event.metadata.get("event", event.label),
                    detail=event.metadata.get("detail", ""),
                    status=event.metadata.get("status", ""),
                ))
                asyncio.create_task(self._save(state))

            elif meta_type == "compact_summary":
                summary = event.metadata.get("summary", "")
                covers_steps = event.metadata.get("covers_steps", 0)
                if summary:
                    state.compact_summary = summary
                self._append_history(state, _HistoryEntry(
                    ts=_ts(),
                    event=f"Context compacted (steps 1–{covers_steps})",
                    detail="Conversation history compressed to reduce token usage.",
                    status="done",
                ))
                asyncio.create_task(self._save(state))

    async def get(self, session_id: str, **kwargs) -> Optional[str]:
        """Return a text summary of session state, suitable for prompt injection."""
        async with self._registry_lock:
            state = self._sessions.get(session_id)
        if state is None:
            return None
        return state.to_text() or None

    async def get_html(self, session_id: str) -> Optional[str]:
        """Return the full HTML content of the memory file for this session."""
        async with self._registry_lock:
            state = self._sessions.get(session_id)
        if state is None or not os.path.exists(state.file_path):
            return None
        return await asyncio.to_thread(_read_sync, state.file_path)

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
                model=self.model_name,
                messages=[
                    SystemMessage(content="You are a concise execution history summarizer."),
                    HumanMessage(content=prompt),
                ],
            )
            state.history_summary = response.message.strip()
            await self._save(state)
            logger.info(f"| 🗜️ FileSystemMemory: compressed {len(overflow)} history entries for {state.session_id}")
        except Exception as e:
            logger.warning(f"| ⚠️ FileSystemMemory: history compression failed ({e})")
        finally:
            state._compressing = False

    async def _apply_todos(self, state: _SessionState, raw_todos: List[Dict[str, Any]]) -> None:
        """Summarise long todo descriptions, update state, then save."""
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
                model=self.model_name,
                messages=[
                    SystemMessage(content="You are a concise summariser."),
                    HumanMessage(content=(
                        f"Summarise this task description in at most {self.max_todo_length} characters. "
                        f"Return ONLY the summary, no extra text.\n\n{description}"
                    )),
                ],
            )
            return response.message.strip()[: self.max_todo_length]
        except Exception as e:
            logger.warning(f"| ⚠️ FileSystemMemory: summarisation failed ({e}), truncating")
            return description[: self.max_todo_length]

    async def _save(self, state: _SessionState) -> None:
        """Render HTML and write to disk; serialised per session via _write_lock."""
        html = state.render()
        async with state._write_lock:
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
