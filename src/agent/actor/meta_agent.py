"""MetaAgent — event-driven orchestrator on the unified Actor model.

Architecture
------------
Two-path interface (from Agent base class):

  __call__   → one-shot via runtime_manager.invoke
  on_start   → init MetaState, kick off step-0 plan (returns None = async)
  on_event   → handle all inbox messages inline:
                 SubtaskDone/Failed → record + refresh + maybe think
                 EscalationMessage  → store + always think
  on_stop    → lifecycle hooks + trace after _finish

Sub-agents post Done/Failed/Escalation directly to MetaAgent's ref._inbox.
ref.name == session_id — no separate session registry needed.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.agent.server import agent_manager
from src.agent.types import Agent, AgentContext
from src.constraint import render_status_text
from src.response.types import Response, ResponseType
from src.config import config
from src.hook.server import hook_manager
from src.hook.types import HookEvent, HookContext, HookDecision
from src.logger import logger
from src.memory import memory_manager
from src.model import model_manager
from src.prompt import prompt_manager
from src.registry import AGENT
from src.runtime import runtime_manager, BaseMessage, AgentRef
from src.task.types import TaskStatus, SubTaskCategory
from src.trace.server import trace_manager
from src.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_start_event,
    agent_end_event,
)
from src.utils.name_utils import make_id

_SUBTASK_EVENTS = frozenset(
    {
        "subtask_planned",
        "subtask_dispatch",
        "subtask_done",
        "subtask_failed",
        "subtask_cancelled",
    }
)


# ---------------------------------------------------------------------------
# Sub-task spec & record
# ---------------------------------------------------------------------------


class SubTaskInput(BaseModel):
    task: str
    files: List[str] = Field(default_factory=list)
    args: Dict[str, Any] = Field(default_factory=dict)


class SubTaskSpec(BaseModel):
    id: str = Field(default_factory=lambda: make_id())
    category: SubTaskCategory = SubTaskCategory.ACTOR
    kind: str = Field(default="agent", description="What to invoke: agent / tool / skill / connector.")
    name: str = Field(description="Name of the sub-agent / tool / skill / connector to invoke.")
    input: SubTaskInput


class TaskSpec(BaseModel):
    """LLM-facing task spec (no id/status — all LLM-output tasks are pending).

    Like Claude Code, MetaAgent invokes four kinds of capability, chosen via ``kind``:
    - ``agent``: dispatch a self-contained ``task`` to a registered sub-agent (default).
    - ``tool``: call a registered tool directly with ``args``.
    - ``skill``: invoke a skill by name (``args`` optional).
    - ``connector``: call an MCP connector action; ``args`` = {"action": ..., "args": {...}}.
    """

    kind: str = Field(default="agent", description="One of: agent, tool, skill, connector.")
    name: str = Field(description="Exact registered name of the sub-agent / tool / skill / connector.")
    category: SubTaskCategory = SubTaskCategory.ACTOR
    task: str = Field(default="", description="For kind=agent: the self-contained instruction. Ignored for tool/skill/connector.")
    files: List[str] = Field(default_factory=list)
    args: Dict[str, Any] = Field(default_factory=dict, description="For kind=tool/skill/connector: the call arguments.")


class PlanRound(BaseModel):
    goal: str = Field(default="")
    tasks: List[TaskSpec] = Field(default_factory=list)


class SubTaskRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    spec: SubTaskSpec
    status: TaskStatus = TaskStatus.PENDING
    session_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[str] = None  # latest snapshot from MonitorProgressMessage
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def mark_running(self, session_id: str) -> None:
        self.status = TaskStatus.RUNNING
        self.session_id = session_id
        self.started_at = datetime.now(timezone.utc)

    def mark_done(self, result: str) -> None:
        self.status = TaskStatus.DONE
        self.result = result
        self.finished_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error
        self.finished_at = datetime.now(timezone.utc)

    def mark_cancelled(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.finished_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Sub-task event messages (posted to MetaAgent's AgentRef._inbox)
# ---------------------------------------------------------------------------


class _SubtaskMessage(BaseMessage):
    task_id: str
    agent_name: str
    session_id: str


class SubtaskDoneMessage(_SubtaskMessage):
    result: str = ""


class SubtaskFailedMessage(_SubtaskMessage):
    error: str = ""


class EscalationMessage(_SubtaskMessage):
    """Sub-agent is blocked; reply_future carries MetaAgent's guidance back."""

    reason: str = ""
    situation: str = ""
    suggestion: str = ""

    @property
    def text(self) -> str:
        body = f"Reason: {self.reason}\nSituation: {self.situation}"
        if self.suggestion:
            body += f"\nSuggestion: {self.suggestion}"
        return body


class MonitorProgressMessage(_SubtaskMessage):
    """Periodic progress update from MonitorAgent while a subprocess is running."""

    pid: int
    status: Literal["running", "completed", "failed", "timeout"]
    elapsed: float
    recent_output: str = ""
    exit_code: Optional[int] = None


# ---------------------------------------------------------------------------
# Structured LLM outputs
# ---------------------------------------------------------------------------


class EscalationReply(BaseModel):
    task_id: str = Field(description="Exact task_id from the ESCALATE event.")
    reply: str = Field(
        description="Concrete, actionable guidance for the blocked sub-agent."
    )


class MetaReactOutput(BaseModel):
    reasoning: str
    decision: Literal["continue", "stop"]
    plan: List[PlanRound] = Field(default_factory=list)
    escalation_replies: List[EscalationReply] = Field(default_factory=list)
    final_answer: str = ""


# ---------------------------------------------------------------------------
# Per-invocation state
# ---------------------------------------------------------------------------

_TERMINAL = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


class PlanRoundRecord(BaseModel):
    goal: str = ""
    task_ids: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING


class MetaState(BaseModel):
    """Complete, isolated state for one MetaAgent invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    user_task: str
    user_files: List[str] = Field(default_factory=list)
    plan: List[PlanRoundRecord] = Field(default_factory=list)
    subtask_records: Dict[str, SubTaskRecord] = Field(default_factory=dict)
    final_answer: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Step counter and ctx — updated on each event.
    step: int = 0

    # Transient — excluded from serialization.
    _ctx: Optional[AgentContext] = PrivateAttr(default=None)
    _running_tasks: Dict[str, asyncio.Task] = PrivateAttr(default_factory=dict)
    _pending_escalations: Dict[str, "EscalationMessage"] = PrivateAttr(
        default_factory=dict
    )
    _events_since_think: List[BaseMessage] = PrivateAttr(default_factory=list)
    _parent_ref: Optional[AgentRef] = PrivateAttr(default=None)
    _project_context: str = PrivateAttr(default="")
    _available_agents: str = PrivateAttr(default="")
    _t0: float = PrivateAttr(default=0.0)
    # Reviewer stop-gate: how many reviewer rounds have been auto-injected before stop,
    # and the latest parsed reviewer verdict (stop | continue | evolve | "").
    _review_cycles: int = PrivateAttr(default=0)
    _reviewer_ran: bool = PrivateAttr(default=False)
    _last_review_verdict: str = PrivateAttr(default="")

    # ------------------------------------------------------------------
    # Round queries
    # ------------------------------------------------------------------

    def active_round(self) -> Optional[PlanRoundRecord]:
        for r in self.plan:
            if r.status == TaskStatus.RUNNING:
                return r
        return None

    def next_pending_round(self) -> Optional[PlanRoundRecord]:
        for r in self.plan:
            if r.status == TaskStatus.PENDING:
                return r
        return None

    def refresh_round_statuses(self) -> None:
        for r in self.plan:
            if r.status == TaskStatus.RUNNING and all(
                self.subtask_records[t].status in _TERMINAL
                for t in r.task_ids
                if t in self.subtask_records
            ):
                r.status = TaskStatus.DONE

    def has_pending_or_running(self) -> bool:
        return any(
            r.status in (TaskStatus.PENDING, TaskStatus.RUNNING) for r in self.plan
        )

    def round_display_status(self, r: PlanRoundRecord) -> str:
        if r.status == TaskStatus.DONE:
            statuses = [
                self.subtask_records[t].status
                for t in r.task_ids
                if t in self.subtask_records
            ]
            if any(s == TaskStatus.FAILED for s in statuses):
                return "failed"
            if statuses and all(s == TaskStatus.CANCELLED for s in statuses):
                return "cancelled"
        return r.status.value

    def done(self) -> List[SubTaskRecord]:
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.DONE]

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# MetaAgent
# ---------------------------------------------------------------------------


@AGENT.register_module(force=True)
class MetaAgent(Agent):
    """Event-driven orchestrator: one inbox, messages dispatched via on_start / on_event."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="meta_agent")
    description: str = Field(
        default="Orchestrator that decomposes tasks, dispatches sub-agents concurrently, "
        "reacts to results, and triggers self-evolution when agents underperform."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False)

    _state: Optional[MetaState] = PrivateAttr(default=None)

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        max_step: int = 50,
        enable_evolving: bool = False,
        **kwargs,
    ):
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "meta_agent",
            memory_name=memory_name,
            max_step=max_step,
            enable_evolving=enable_evolving,
            **kwargs,
        )
        from pathlib import Path

        self.project_root = str(Path(__file__).resolve().parents[3])

    # ------------------------------------------------------------------
    # Path 1: direct call
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: Optional[str] = None,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Response:
        return await runtime_manager.invoke(
            self, task=task, files=files, ctx=ctx, **kwargs
        )

    # ------------------------------------------------------------------
    # Path 2: event-driven lifecycle
    # ------------------------------------------------------------------

    async def on_start(
        self,
        task: str,
        files: Optional[List[str]],
        ctx: Optional[AgentContext],
        ref: AgentRef,
    ) -> Optional[Response]:
        """Init state, kick off step-0 plan.  Returns None — resolved later via _finish."""
        session_id = ref.name
        logger.info(f"| 🧠 MetaAgent starting: {task}")

        state = MetaState(session_id=session_id, user_task=task, user_files=files or [])
        state._parent_ref = ref
        state._ctx = ctx
        state._t0 = time.monotonic()
        self._state = state

        try:
            with open(
                os.path.join(self.project_root, "PROJECT.md"), "r", encoding="utf-8"
            ) as f:
                state._project_context = f.read()
        except Exception:
            state._project_context = "(PROJECT.md not found)"

        agent_lines: List[str] = []
        for aname in await agent_manager.list():
            if aname == self.name:
                continue
            ainfo = await agent_manager.get_info(aname)
            if ainfo:
                agent_lines.append(f"- {ainfo.name}: {ainfo.description}")
        state._available_agents = "\n".join(agent_lines) or "[No sub-agents registered]"

        _hook_ctx = HookContext(id=session_id, name="memory_hook")
        _hook_start = {
            "event": HookEvent.ON_START,
            "agent_name": self.name,
            "task_id": session_id,
            "task": task,
            "memory_name": self.memory_name,
            "use_memory": self.use_memory,
        }
        await hook_manager(name="memory_hook", input=_hook_start, ctx=_hook_ctx)
        await hook_manager(
            name="trace_hook",
            input=_hook_start,
            ctx=HookContext(id=session_id, name="trace_hook"),
        )
        await trace_manager.emit(
            agent_start_event(
                session_id=session_id,
                task_id=session_id,
                agent_name=self.name,
                task_content=task,
            )
        )
        await self._record(
            session_id, 0, "start", "START", "requesting initial plan", "running"
        )

        try:
            await self._think_and_act(state, events=[])
        except Exception as exc:
            logger.error(f"| ❌ MetaAgent step 0 failed: {exc}", exc_info=True)
            await self._finish(error=str(exc))
            return None

        if not state.has_pending_or_running():
            state.final_answer = state.final_answer or self._join_results(state)
            await self._finish()

        return None

    async def on_event(self, msg: BaseMessage, ref: AgentRef) -> None:
        """Handle all inbox messages: subtask completions and escalations."""
        state = self._state
        if state is None:
            if (
                isinstance(msg, EscalationMessage)
                and msg.reply_future
                and not msg.reply_future.done()
            ):
                msg.reply_future.set_result("No active orchestration. Stop gracefully.")
            return

        if isinstance(msg, SubtaskDoneMessage):
            state.step += 1
            if rec := state.subtask_records.get(msg.task_id):
                rec.mark_done(msg.result)
                # Track reviewer verdicts so the stop-gate knows the run was reviewed.
                if rec.spec.name == "reviewer_agent":
                    state._reviewer_ran = True
                    state._last_review_verdict = self._parse_review_verdict(msg.result)
                    logger.info(f"| 🔎 Reviewer verdict: {state._last_review_verdict or 'unparsed'}")
            await self._record(
                state.session_id,
                state.step,
                "subtask_done",
                f"DONE {msg.task_id}",
                msg.result,
                "done",
                {"subtask_id": msg.task_id, "agent_name": msg.agent_name},
            )
            logger.info(f"| ✅ Subtask done: {msg.task_id}")
            state.refresh_round_statuses()
            state.touch()
            state._events_since_think.append(msg)
            # Only think at round boundary or if escalations are waiting
            if state.active_round() is not None and not state._pending_escalations:
                return

        elif isinstance(msg, SubtaskFailedMessage):
            state.step += 1
            err = msg.error or "unknown error"
            if rec := state.subtask_records.get(msg.task_id):
                rec.mark_failed(err)
            await self._record(
                state.session_id,
                state.step,
                "subtask_failed",
                f"FAILED {msg.task_id}",
                err,
                "failed",
                {"subtask_id": msg.task_id, "agent_name": msg.agent_name},
            )
            logger.warning(f"| ❌ Subtask failed: {msg.task_id} — {err}")
            state.refresh_round_statuses()
            state.touch()
            state._events_since_think.append(msg)
            if state.active_round() is not None and not state._pending_escalations:
                return

        elif isinstance(msg, EscalationMessage):
            state.step += 1
            state._pending_escalations[msg.task_id] = msg
            await self._record(
                state.session_id,
                state.step,
                "escalation",
                f"ESCALATE {msg.task_id} ({msg.agent_name})",
                msg.text,
                "running",
                {
                    "task_id": msg.task_id,
                    "agent_name": msg.agent_name,
                    "message": msg.text,
                },
            )
            state.touch()
            state._events_since_think.append(msg)
            # Always think immediately on escalation

        elif isinstance(msg, MonitorProgressMessage):
            if rec := state.subtask_records.get(msg.task_id):
                elapsed_str = f"{msg.elapsed:.0f}s"
                rec.progress = f"[{msg.status}] elapsed={elapsed_str} pid={msg.pid}"
                if msg.recent_output:
                    rec.progress += f"\n{msg.recent_output[-500:]}"
            logger.info(
                f"| 📡 MonitorAgent [{msg.task_id}] pid={msg.pid} "
                f"status={msg.status} elapsed={msg.elapsed:.0f}s"
            )
            state._events_since_think.append(msg)
            return  # Progress updates do not trigger a think step

        else:
            logger.warning(
                f"| ⚠️ MetaAgent: unhandled message type {type(msg).__name__}"
            )
            return

        # --- think ---
        if state.step > self.max_step:
            logger.warning(f"| 🛑 Reached max steps ({self.max_step})")
            for e in state._pending_escalations.values():
                if e.reply_future and not e.reply_future.done():
                    e.reply_future.set_result("Max steps reached. Stop gracefully.")
            state._pending_escalations.clear()
            state.final_answer = state.final_answer or self._join_results(state)
            await self._finish()
            return

        events = list(state._events_since_think)
        state._events_since_think.clear()
        try:
            await self._think_and_act(state, events=events)
        except Exception as exc:
            logger.error(f"| ❌ MetaAgent think failed: {exc}", exc_info=True)
            if (
                isinstance(msg, EscalationMessage)
                and msg.reply_future
                and not msg.reply_future.done()
            ):
                msg.reply_future.set_result("Error in MetaAgent. Stop gracefully.")
            else:
                state.final_answer = str(exc)

        if state.final_answer is not None:
            await self._finish()

    async def on_stop(self, result: Response, ctx: Optional[AgentContext]) -> None:
        """Lifecycle hooks + trace — called by _finish while self._state is still set."""
        state = self._state
        if state is None:
            return
        sid = state.session_id
        # Clean up constraint session state
        for c in self.constraints:
            c._cleanup(sid)
        self._pending_step_tokens.pop(sid, None)
        _hctx_mem = HookContext(id=sid, name="memory_hook")
        _hctx_trace = HookContext(id=sid, name="trace_hook")
        if self.memory_name:
            await hook_manager(
                name="memory_hook",
                ctx=_hctx_mem,
                input={
                    "event": HookEvent.ON_CALL,
                    "agent_name": self.name,
                    "task_id": sid,
                    "use_memory": self.use_memory,
                    "memory_name": self.memory_name,
                    "final_result": result.message,
                    "success": result.success,
                },
            )
        _hook_stop = {
            "event": HookEvent.ON_STOP,
            "agent_name": self.name,
            "task_id": sid,
            "result": result.message,
            "memory_name": self.memory_name,
            "use_memory": self.use_memory,
        }
        await hook_manager(name="memory_hook", input=_hook_stop, ctx=_hctx_mem)
        await hook_manager(name="trace_hook", input=_hook_stop, ctx=_hctx_trace)
        await trace_manager.emit(
            agent_end_event(
                session_id=sid,
                task_id=sid,
                agent_name=self.name,
                success=result.success,
                result=result.message,
                duration_ms=(time.monotonic() - state._t0) * 1000,
            )
        )

    # ------------------------------------------------------------------
    # Core: think → apply LLM decision
    # ------------------------------------------------------------------

    async def _think_and_act(self, state: MetaState, events: List[BaseMessage]) -> None:
        # --- Constraint checks (delegated to the constraint hook on PRE_STEP) ---
        constraint_statuses: List[Dict[str, Any]] = []
        if self.constraints:
            constraint_result = await hook_manager(
                name="constraint_hook",
                input={
                    "event": HookEvent.PRE_STEP,
                    "agent_name": self.name,
                    "task_id": state.session_id,
                    "constraint_names": [c.name for c in self.constraints],
                    "check_input": {
                        "token": self._pending_step_tokens.pop(state.session_id, 0),
                        "max_step": self.max_step,
                        "max_token": self.max_token,
                        "max_second": self.timeout,
                    },
                },
                ctx=state._ctx,
            )
            if constraint_result.decision == HookDecision.BLOCK:
                for c in self.constraints:
                    c._cleanup(state.session_id)
                logger.warning(f"| 🛑 MetaAgent constraint violated: {constraint_result.reason}")
                state.final_answer = self._join_results(state) or constraint_result.reason
                state.touch()
                return
            constraint_statuses = constraint_result.constraint_status or []

        messages = await self._get_messages(state, events, constraint_status=constraint_statuses)
        response = await model_manager(
            name=self.model_name,
            input={"messages": messages, "response_format": MetaReactOutput},
            ctx=state._ctx,
        )
        if response.usage:
            self._pending_step_tokens[state.session_id] = response.usage.total
        if response.parsed_model:
            react = response.parsed_model
        else:
            try:
                react = MetaReactOutput.model_validate_json(response.message)
            except Exception:
                raise RuntimeError(f"LLM structured output failed: {response.message}")
        logger.info(f"| 🤔 React decision: {react.decision}")

        # Resolve pending escalations from LLM replies
        _DEFAULT = "No specific guidance available. Use your best judgement or stop gracefully."
        explicit = {er.task_id: er.reply for er in react.escalation_replies}
        for tid in list(state._pending_escalations.keys()):
            e = state._pending_escalations.pop(tid)
            if not (e.reply_future and not e.reply_future.done()):
                continue
            reply = explicit.get(tid, _DEFAULT)
            e.reply_future.set_result(reply)
            await self._record(
                state.session_id,
                state.step,
                "escalation_reply",
                f"REPLY {tid}" if tid in explicit else f"REPLY {tid} (default)",
                reply if tid in explicit else "no guidance",
                "",
                {"task_id": tid, "reply": reply},
            )

        if state.active_round() is not None:  # mid-round: defer re-planning
            state.touch()
            return

        if react.decision == "stop":
            review_round = self._stop_review_gate(state)
            if review_round is not None:
                logger.info(
                    f"| 🔎 Stop gated (cycle {state._review_cycles}): dispatching reviewer_agent "
                    f"before finishing"
                )
                await self._dispatch(state, [review_round])
                state.touch()
                return
            state.final_answer = react.final_answer or self._join_results(state)
            state.touch()
            return

        await self._dispatch(state, react.plan)

        if not state.has_pending_or_running() and not state.final_answer:
            state.final_answer = react.final_answer or self._join_results(state)
        state.touch()

    # ------------------------------------------------------------------
    # Finish: cancel subtasks, resolve future, teardown
    # ------------------------------------------------------------------

    async def _finish(self, *, error: Optional[str] = None) -> None:
        state = self._state
        if state is None:
            return

        # Cancel running subtasks
        for tid, t in list(state._running_tasks.items()):
            if not t.done():
                t.cancel()
                logger.info(f"| 🛑 Cancelled subtask {tid}")
        await asyncio.gather(*state._running_tasks.values(), return_exceptions=True)
        for rec in state.subtask_records.values():
            if rec.status == TaskStatus.RUNNING:
                rec.mark_cancelled()
                await self._record(
                    state.session_id,
                    0,
                    "subtask_cancelled",
                    f"CANCELLED {rec.spec.id}",
                    "",
                    "cancelled",
                    {"subtask_id": rec.spec.id, "agent_name": rec.spec.name},
                )

        success = error is None
        final_answer = error or state.final_answer or self._join_results(state)
        sid = state.session_id

        memory_path = ""
        if self.memory_name:
            try:
                info = await memory_manager.get_info(self.memory_name)
                if info and info.instance:
                    memory_path = os.path.join(
                        info.instance.base_dir, f"{self.name}_{sid}.memory.html"
                    )
            except Exception:
                pass

        logger.info(f"| ✅ MetaAgent done (success={success})")
        result = Response(type=ResponseType.AGENT, 
            success=success,
            message=final_answer or "",
            data={
                "session_id": sid,
                "memory_path": memory_path,
                "state": state.model_dump(),
            },
        )

        ref = state._parent_ref
        if (
            ref is not None
            and ref._pending_reply is not None
            and not ref._pending_reply.done()
        ):
            ref._pending_reply.set_result(result)
            ref._pending_reply = None

        await self.on_stop(
            result, state._ctx
        )  # on_stop reads self._state — must come before reset
        self._state = None

    # ------------------------------------------------------------------
    # Dispatch: reconcile plan + launch subtasks
    # ------------------------------------------------------------------

    async def _dispatch(self, state: MetaState, react_plan: List[PlanRound]) -> None:
        if react_plan:
            frozen = sum(
                1
                for r in state.plan
                if r.status in (TaskStatus.RUNNING, TaskStatus.DONE)
            )
            for rec in state.plan[frozen:]:
                for tid in rec.task_ids:
                    state.subtask_records.pop(tid, None)
            state.plan = state.plan[:frozen]

            for round_spec in react_plan:
                round_no = len(state.plan) + 1
                rec = PlanRoundRecord(goal=round_spec.goal)
                for task_spec in round_spec.tasks:
                    spec = SubTaskSpec(
                        kind=task_spec.kind,
                        category=task_spec.category,
                        name=task_spec.name,
                        input=SubTaskInput(
                            task=task_spec.task,
                            files=task_spec.files,
                            args=task_spec.args,
                        ),
                    )
                    state.subtask_records[spec.id] = SubTaskRecord(spec=spec)
                    rec.task_ids.append(spec.id)
                    await self._record(
                        state.session_id,
                        state.step,
                        "subtask_planned",
                        f"PLANNED {spec.id}",
                        f"{spec.input.task} → {spec.name}",
                        "pending",
                        {
                            "subtask_id": spec.id,
                            "agent_name": spec.name,
                            "category": spec.category.value,
                            "round": round_no,
                            "task": spec.input.task,
                            "round_label": round_spec.goal or f"Round {round_no}",
                        },
                    )
                if not rec.task_ids:
                    logger.warning(f"| ⚠️ Round {round_no} has no tasks — skipped.")
                    continue
                state.plan.append(rec)

        rnd = state.next_pending_round()
        if rnd is None:
            return

        rnd.status = TaskStatus.RUNNING
        parent_ref = state._parent_ref
        for tid in rnd.task_ids:
            record = state.subtask_records[tid]
            record.mark_running(f"{record.spec.name}-{make_id()}")
            state._running_tasks[tid] = asyncio.create_task(
                self._run_subtask(record, state, parent_ref),
                name=f"subtask-{tid}",
            )
            await self._record(
                state.session_id,
                state.step,
                "subtask_dispatch",
                f"DISPATCH {tid}",
                f"{record.spec.name}: {record.spec.input.task}",
                "running",
                {"subtask_id": tid, "agent_name": record.spec.name},
            )
            logger.info(
                f"| 🚀 Dispatched '{record.spec.input.task}' → {record.spec.name} [{tid}]"
            )

    async def _run_subtask(
        self, record: SubTaskRecord, state: MetaState, parent_ref: AgentRef
    ) -> None:
        """Run one sub-agent; post Done/Failed to parent_ref._inbox."""
        task_id = record.spec.id
        session_id = record.session_id or record.spec.id
        agent_name = record.spec.name

        kind = getattr(record.spec, "kind", "agent")
        ctx = AgentContext(
            id=session_id,
            work_dir=self.base_dir,
            parent_session_id=parent_ref.name,
            subtask_id=task_id,
        )
        existing_files = [
            f for f in (record.spec.input.files or []) if os.path.exists(f)
        ]
        args = dict(record.spec.input.args or {})

        # A sub-task may pin which skills/tools/connectors the sub-agent sees — e.g.
        # MetaAgent runs a with-X vs baseline probe by dispatching the same agent twice
        # with different allowlists. Honor optional `{skill,tool,connector}_allowlist` in
        # args by injecting them into the sub-agent's ctx (consumed by Agent._get_*_context).
        for _al in ("skill_allowlist", "tool_allowlist", "connector_allowlist"):
            _val = args.pop(_al, None)
            if _val is not None:
                ctx.extra[_al] = _val

        try:
            if kind == "agent":
                extra_kwargs: Dict[str, Any] = {"parent_ref": parent_ref}
                sub_agent = await agent_manager.get(agent_name)
                if sub_agent is None:
                    raise ValueError(f"No registered agent named {agent_name!r}")
                response = await runtime_manager.invoke(
                    sub_agent,
                    name=session_id,
                    task=record.spec.input.task,
                    files=existing_files or None,
                    ctx=ctx,
                    **extra_kwargs,
                )
                result_msg = response.message
            elif kind == "tool":
                from src.tool.server import tool_manager
                resp = await tool_manager(name=agent_name, input=args, ctx=ctx)
                result_msg = resp.message
            elif kind == "skill":
                from src.skill.server import skill_manager
                resp = await skill_manager(name=agent_name, input=args, ctx=ctx)
                result_msg = resp.message
            elif kind == "connector":
                from src.connector.server import connector_manager
                resp = await connector_manager(name=agent_name, input=args, ctx=ctx)
                result_msg = resp.message
            else:
                raise ValueError(f"Unknown task kind {kind!r} (expected agent/tool/skill/connector)")

            await parent_ref._inbox.put(
                SubtaskDoneMessage(
                    task_id=task_id,
                    agent_name=agent_name,
                    session_id=session_id,
                    result=result_msg,
                )
            )
        except asyncio.CancelledError:
            logger.info(f"| ✋ Subtask {task_id} cancelled")
        except Exception as exc:
            logger.error(f"| ❌ Subtask {task_id} failed: {exc}", exc_info=True)
            await parent_ref._inbox.put(
                SubtaskFailedMessage(
                    task_id=task_id,
                    agent_name=agent_name,
                    session_id=session_id,
                    error=str(exc),
                )
            )

    # ------------------------------------------------------------------
    # Prompt assembly: situation + memory → messages
    # ------------------------------------------------------------------

    async def _get_messages(
        self,
        state: MetaState,
        events: List[BaseMessage],
        constraint_status: Optional[List[Dict[str, Any]]] = None,
    ) -> list:
        # Build the agent_context sub-modules. Each becomes its own nested tag
        # in the user template (see meta_agent.html). The old single "situation"
        # blob is split into the delta (events) and the snapshot (plan) so each
        # is read for what it is.

        # ── task: the user objective, plus any input files (folded in — they
        #    are part of the assignment, not a separate block) ──
        existing = [f for f in state.user_files if os.path.exists(f)]
        task_text = state.user_task
        if existing:
            task_text += "\n\n**Input files:**\n" + "\n".join(f"- {f}" for f in existing)

        # ── events: what changed since the last decision (the delta) ──
        event_lines: List[str] = []
        escalations = [e for e in events if isinstance(e, EscalationMessage)]
        completions = [
            e
            for e in events
            if isinstance(e, (SubtaskDoneMessage, SubtaskFailedMessage))
        ]
        progresses = [e for e in events if isinstance(e, MonitorProgressMessage)]
        if escalations:
            event_lines.append("### Escalations Requiring Reply")
            for e in escalations:
                event_lines.append(f"- ESCALATE [{e.task_id}] ({e.agent_name}): {e.text}")
        if completions:
            event_lines.append("### Recent Events")
            for e in completions:
                if isinstance(e, SubtaskDoneMessage):
                    event_lines.append(f"- DONE [{e.task_id}]: {e.result}")
                else:
                    event_lines.append(f"- FAILED [{e.task_id}]: {e.error}")
        if progresses:
            event_lines.append("### Monitor Progress Updates")
            for e in progresses:
                line = f"- [{e.status.upper()}] {e.task_id} ({e.agent_name}) pid={e.pid} elapsed={e.elapsed:.0f}s"
                if e.exit_code is not None:
                    line += f" exit={e.exit_code}"
                event_lines.append(line)
                if e.recent_output:
                    event_lines.append(f"  Recent output: {e.recent_output[:300]}")
        events_text = "\n".join(event_lines) if event_lines else "[No new events this step.]"

        # ── plan: the current plan snapshot (rounds run in order; tasks within
        #    a round run concurrently) ──
        plan_lines: List[str] = []
        if not state.plan:
            plan_lines.append(
                "No plan yet. Produce the initial plan as a list of rounds."
                if not events
                else "(no plan yet)"
            )
        for i, rec in enumerate(state.plan, 1):
            goal = f" — {rec.goal}" if rec.goal else ""
            plan_lines.append(
                f"**Round {i}** [{state.round_display_status(rec).upper()}]{goal}"
            )
            for tid in rec.task_ids:
                r = state.subtask_records.get(tid)
                if not r:
                    continue
                line = f"  - [{r.status.value.upper()}] {tid} ({r.spec.name}): {r.spec.input.task}"
                if r.result:
                    line += f" → {r.result}"
                if r.error:
                    line += f" ✗ {r.error}"
                if r.progress:
                    line += f" [progress: {r.progress[:150]}]"
                plan_lines.append(line)
        plan_text = "\n".join(plan_lines)

        ctx = state._ctx
        work_dir = str(ctx.work_dir if ctx and ctx.work_dir else self.base_dir)

        # Produce the universal agent-context sub-modules with the SAME base-class
        # methods every other agent uses, so MetaAgent's input structure is identical:
        #   _get_agent_context     → step_info, memory_context, constraint_text, workspace, errors
        #   _get_tool_context      → tool_context, available_tools
        #   _get_skill_context     → skill_context, available_skills
        #   _get_connector_context → connector_context, available_connectors
        # On top, MetaAgent adds only its orchestration sub-modules: available_agents, events, plan.
        base = await self._get_agent_context(
            task_text, step_number=state.step, ctx=ctx, constraint_status=constraint_status
        )
        tool_mod = await self._get_tool_context(ctx=ctx)
        skill_mod = await self._get_skill_context(ctx=ctx)
        conn_mod = await self._get_connector_context(ctx=ctx)

        response = await prompt_manager(
            name=self.prompt_name,
            input={
                "system_modules": dict(
                    project_root=self.project_root,
                    project_context=state._project_context,
                    work_dir=work_dir,
                ),
                "agent_modules": dict(
                    task=task_text,
                    **base,
                    **tool_mod,
                    **skill_mod,
                    **conn_mod,
                    available_agents=state._available_agents,
                    events=events_text,
                    plan=plan_text,
                ),
            },
        )
        if not response.success:
            raise ValueError(response.message)

        return response.data["messages"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _record(
        self,
        session_id: str,
        step: int,
        action: str,
        label: str,
        detail: str = "",
        status: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.memory_name:
            payload: Dict[str, Any] = {
                "event": HookEvent.ON_CALL,
                "agent_name": self.name,
                "task_id": session_id,
                "use_memory": self.use_memory,
                "memory_name": self.memory_name,
                "note": {"event": label, "detail": detail, "status": status},
            }
            if action in _SUBTASK_EVENTS and data:
                payload["subtask_event"] = {"action": action, "data": data}
            await hook_manager(
                name="memory_hook",
                input=payload,
                ctx=HookContext(id=session_id, name="memory_hook"),
            )
        await trace_manager.emit(
            TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=session_id,
                agent_name=self.name,
                step_number=step,
                action_name=action,
                label=label,
                input=data or {},
            )
        )

    def _join_results(self, state: MetaState) -> str:
        return (
            "\n\n".join(
                f"[{r.spec.input.task}]\n{r.result}" for r in state.done() if r.result
            )
            or "Task completed."
        )

    # ------------------------------------------------------------------
    # Reviewer stop-gate (advisory reviewer, softly bound to `stop`)
    # ------------------------------------------------------------------
    #: How many reviewer rounds may be auto-injected before a stop is allowed regardless
    #: (prevents a review↔re-plan loop from never terminating).
    MAX_STOP_REVIEWS: int = 2

    @staticmethod
    def _parse_review_verdict(text: str) -> str:
        """Extract 'stop' | 'continue' | 'evolve' from a reviewer's report (or '')."""
        if not text:
            return ""
        m = re.search(r"RECOMMENDATION[:\s\-]*\**\s*(stop|continue|evolve)", text, re.IGNORECASE)
        return m.group(1).lower() if m else ""

    def _stop_review_gate(self, state: MetaState) -> Optional[PlanRound]:
        """Decide whether a `stop` must first pass an independent review.

        Returns a reviewer PlanRound to run instead of stopping, or None to allow the
        stop. Bounded by ``MAX_STOP_REVIEWS`` so it can never loop forever; skipped for
        trivial runs (no sub-task actually completed) and once the reviewer has returned
        a `stop` verdict.
        """
        did_work = any(r.status == TaskStatus.DONE for r in state.subtask_records.values())
        if not did_work:
            return None  # trivial / no substantive work — nothing to review
        if state._review_cycles >= self.MAX_STOP_REVIEWS:
            return None  # cap reached — allow stop to avoid an unbounded review loop
        # Keep gating only when a review actively says "not done" (continue/evolve). A
        # review that passed ("stop") or was inconclusive/unparsed ("") allows the stop —
        # don't burn the cap re-reviewing when there is no actionable verdict to act on.
        if state._reviewer_ran and state._last_review_verdict not in ("continue", "evolve"):
            return None

        state._review_cycles += 1
        summary = self._join_results(state)
        if len(summary) > 2000:
            summary = summary[:2000] + " …(truncated)"
        task = (
            "Independently review whether the user's task was actually accomplished. "
            "Verify the real deliverable HANDS-ON (reach the URL / run it / open the file) — "
            "do not trust claims. List remaining defects, judge whether any self-evolution "
            "helped, and END your report with a line 'RECOMMENDATION: stop | continue | evolve'.\n\n"
            f"USER TASK:\n{state.user_task}\n\nWHAT WAS PRODUCED / EXECUTED (summary):\n{summary}"
        )
        return PlanRound(
            goal="Independent review before finishing",
            tasks=[TaskSpec(
                kind="agent",
                name="reviewer_agent",
                category=SubTaskCategory.EVALUATOR,
                task=task,
            )],
        )
