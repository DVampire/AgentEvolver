"""MetaAgent — orchestrator that decomposes a user task and dispatches sub-agents concurrently.

Execution model
---------------
  user task
      │
      ▼
  _think_and_act()  ──LLM──►  plan (ordered rounds of subtasks) ──► _dispatch()
      │                                                                          │
      │                                                                          ▼
      │                                            runtime_manager.invoke(sub_agent, parent_ref=meta_ref)
      │                                                                          │
      │                                          EscalationMessage    Subtask{Done,Failed}Message
      │                                                  │                       │
      └──────────────────── meta_ref._inbox (asyncio.Queue, runtime-managed) ◄──┘
                                          │
                               _drain() collects events (100 ms batch window)
                                          │
                               _think_and_act() again with events
                                          │
                               all done ──► state.final_answer set ──► AgentResponse

Escalation flow
---------------
  sub-agent blocks  →  EscalationHook does runtime.ask(parent_ref, EscalationMessage(...))
                    →  message lands on meta_ref._inbox; ask awaits reply_future
  meta _think_and_act() sees the EscalationMessage  →  LLM decides reply
                    →  _resolve_escalations sets reply_future.set_result(reply)
  (any escalation the LLM ignored gets a default fallback reply)
  sub-agent's ask unblocks, hook returns the guidance, sub-agent resumes.

Memory & observability
----------------------
  Write  (all paths via MemoryHook):
    lifecycle events  →  MemoryHook  →  GeneralMemorySystem (working memory)
                      →  MemoryHook  →  FileSystemMemory    (HTML per session)
    subtask lifecycle (planned/dispatch/done/failed/cancelled)
                      →  _record() emits ON_CALL + subtask_event payload
                      →  MemoryHook  →  FileSystemMemory    (incremental todo/flowchart updates)

  Read:
    meta_agent._get_messages() reads FileSystemMemory.get() (full HTML of the session file)
                               and injects it as ``memory_context`` on every LLM call.
    sub-agents._get_agent_context() reads GeneralMemorySystem and injects
                                    a text summary into each step's prompt.

Task ID / HTML naming
---------------------
  Each agent run has its own task_id (= AgentContext.id) which also names its HTML file.
  meta_agent : make_id()                        → {id}.memory.html  (root run)
  sub-agent  : mark_running(make_id()) session_id → {id}.memory.html  (one file per subtask)
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.agent.server import agent_manager
from src.agent.types import Agent, AgentContext, AgentResponse, AgentExtra
from src.config import config
from src.hook.server import hook_manager
from src.hook.types import HookEvent, HookContext
from src.logger import logger
from src.memory import memory_manager
from src.model import model_manager
from src.prompt import prompt_manager
from src.registry import AGENT
from src.runtime import runtime_manager, AgentRef, BaseMessage
from src.task.types import TaskStatus, SubTaskCategory
from src.trace.server import trace_manager
from src.trace.types import TraceEvent, TraceEventType, agent_start_event, agent_end_event
from src.utils.name_utils import make_id

# Collect completion events arriving within this window before one LLM REACT call.
_BATCH_WINDOW_S = 0.1

# Subtask lifecycle action names that carry structured data for FileSystemMemory.
_SUBTASK_EVENTS = frozenset({
    "subtask_planned",
    "subtask_dispatch",
    "subtask_done",
    "subtask_failed",
    "subtask_cancelled",
})


# ---------------------------------------------------------------------------
# Sub-task spec & record
# ---------------------------------------------------------------------------

class SubTaskInput(BaseModel):
    task: str
    files: List[str] = Field(default_factory=list)
    target_name: Optional[str] = Field(default=None, description="Name of the tool/agent to optimize or evaluate.")


class SubTaskSpec(BaseModel):
    """Runtime-only subtask spec; ``id`` and round membership are agent-assigned."""
    id: str = Field(default_factory=lambda: make_id())
    category: SubTaskCategory = SubTaskCategory.ACTOR
    name: str = Field(description="Agent name to dispatch this subtask to.")
    input: SubTaskInput


# ---------------------------------------------------------------------------
# LLM-facing plan schema (matches the prompt output-schema exactly)
# ---------------------------------------------------------------------------

class TaskSpec(BaseModel):
    """LLM-facing task spec — no id, no status (all LLM-output tasks are implicitly pending)."""
    name: str = Field(description="Exact registered sub-agent name.")
    category: SubTaskCategory = SubTaskCategory.ACTOR
    task: str = Field(description="Self-contained instruction for the sub-agent.")
    files: List[str] = Field(default_factory=list)
    target_name: Optional[str] = Field(default=None, description="ONLY for evaluator/optimizer/generator tasks — the tool name to evaluate, improve, or create. null for actor tasks.")


class PlanRound(BaseModel):
    """One round: tasks run concurrently; rounds run sequentially by list position."""
    goal: str = Field(default="", description="Short human-readable goal for this round.")
    tasks: List[TaskSpec] = Field(default_factory=list)


class SubTaskRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    spec: SubTaskSpec
    status: TaskStatus = TaskStatus.PENDING
    session_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def mark_running(self, session_id: str) -> None:
        """Transition to RUNNING, recording the assigned session_id and start time."""
        self.status = TaskStatus.RUNNING
        self.session_id = session_id
        self.started_at = datetime.now(timezone.utc)

    def mark_done(self, result: str) -> None:
        """Transition to DONE, storing the result and finish timestamp."""
        self.status = TaskStatus.DONE
        self.result = result
        self.finished_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        """Transition to FAILED, storing the error message and finish timestamp."""
        self.status = TaskStatus.FAILED
        self.error = error
        self.finished_at = datetime.now(timezone.utc)

    def mark_cancelled(self) -> None:
        """Transition to CANCELLED (e.g. MetaAgent shut down mid-run)."""
        self.status = TaskStatus.CANCELLED
        self.finished_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Sub-task event messages (flow into meta_ref._inbox)
# ---------------------------------------------------------------------------

class _SubtaskMessage(BaseMessage):
    """Common header for sub-task event messages addressed to MetaAgent."""
    task_id:    str
    agent_name: str
    session_id: str


class SubtaskDoneMessage(_SubtaskMessage):
    """Sub-agent finished successfully."""
    result: str = ""


class SubtaskFailedMessage(_SubtaskMessage):
    """Sub-agent raised an exception."""
    error: str = ""


class EscalationMessage(_SubtaskMessage):
    """Sub-agent is blocked and is asking MetaAgent for guidance.

    Sent via ``runtime_manager.ask(parent_ref, EscalationMessage(...))``;
    the reply (an str of LLM-decided guidance) is delivered through
    ``BaseMessage.reply_future``, which ``ask`` waits on automatically.
    """
    reason:     str = ""
    situation:  str = ""
    suggestion: str = ""

    @property
    def text(self) -> str:
        body = f"Reason: {self.reason}\nSituation: {self.situation}"
        if self.suggestion:
            body += f"\nSuggestion: {self.suggestion}"
        return body


# ---------------------------------------------------------------------------
# Structured LLM outputs
# ---------------------------------------------------------------------------

class EscalationReply(BaseModel):
    task_id: str = Field(description="Exact task_id from the ESCALATE event.")
    reply: str = Field(description="Concrete, actionable guidance for the blocked sub-agent.")


class MetaReactOutput(BaseModel):
    thinking: str
    decision: Literal["continue", "stop"]
    # New pending rounds only — never re-emit running or done rounds (agent tracks those).
    # Non-empty: replaces all currently pending (not-yet-started) rounds with this list.
    # Empty []: keeps existing pending rounds unchanged (use when no re-planning needed).
    # Always [] when decision == "stop".
    plan: List[PlanRound] = Field(default_factory=list)
    escalation_replies: List[EscalationReply] = Field(default_factory=list)
    final_answer: str = ""


# ---------------------------------------------------------------------------
# Per-invocation state
# ---------------------------------------------------------------------------

_TERMINAL = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


class PlanRoundRecord(BaseModel):
    """Runtime record for one plan round: its task ids and lifecycle status.

    Task specs/results live in MetaState.subtask_records (keyed by id, so the
    runtime can route inbox events); this record just orders them into a round.
    Uses TaskStatus directly to stay consistent with subtask record statuses.
    """
    goal: str = ""
    task_ids: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING


class MetaState(BaseModel):
    """Complete, isolated state for one MetaAgent invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    user_task: str
    user_files: List[str] = Field(default_factory=list)
    # Ordered rounds. Index i (0-based) is round i+1; rounds execute in order.
    plan: List[PlanRoundRecord] = Field(default_factory=list)
    # Flat lookup of every task by id — inbox events are routed by task_id.
    subtask_records: Dict[str, SubTaskRecord] = Field(default_factory=dict)
    final_answer: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Transient concurrency state — excluded from model_dump / serialization.
    # _meta_ref points at MetaAgent's own runtime ref; sub-event channel is
    # _meta_ref._inbox (sub-agents and escalation hook post Subtask*Messages
    # and EscalationMessages here).
    _meta_ref: Optional[AgentRef] = PrivateAttr(default=None)
    _running_tasks: Dict[str, asyncio.Task] = PrivateAttr(default_factory=dict)
    _pending_escalations: Dict[str, "EscalationMessage"] = PrivateAttr(default_factory=dict)
    # Read-once caches: these don't change during a run.
    _project_context: str = PrivateAttr(default="")
    _available_agents: str = PrivateAttr(default="")

    # ------------------------------------------------------------------
    # Round queries
    # ------------------------------------------------------------------

    def active_round(self) -> Optional[PlanRoundRecord]:
        """The round currently dispatched and not yet complete (None at a boundary)."""
        for r in self.plan:
            if r.status == TaskStatus.RUNNING:
                return r
        return None

    def next_pending_round(self) -> Optional[PlanRoundRecord]:
        """First not-yet-dispatched round (callers dispatch only at a boundary)."""
        for r in self.plan:
            if r.status == TaskStatus.PENDING:
                return r
        return None

    def refresh_round_statuses(self) -> None:
        """Mark a running round done once all of its tasks reach a terminal status."""
        for r in self.plan:
            if r.status == TaskStatus.RUNNING and all(
                self.subtask_records[t].status in _TERMINAL
                for t in r.task_ids if t in self.subtask_records
            ):
                r.status = TaskStatus.DONE

    def has_pending_or_running(self) -> bool:
        """True while any round still needs to run."""
        return any(r.status in (TaskStatus.PENDING, TaskStatus.RUNNING) for r in self.plan)

    def round_display_status(self, r: PlanRoundRecord) -> str:
        """Human-readable round status: distinguishes failed/cancelled from clean done."""
        if r.status == TaskStatus.DONE:
            task_statuses = [
                self.subtask_records[t].status
                for t in r.task_ids if t in self.subtask_records
            ]
            if any(s == TaskStatus.FAILED for s in task_statuses):
                return "failed"
            if task_statuses and all(s == TaskStatus.CANCELLED for s in task_statuses):
                return "cancelled"
        return r.status.value  # "pending" | "running" | "done"

    def done(self) -> List[SubTaskRecord]:
        """Return all subtasks that completed successfully."""
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.DONE]

    def touch(self) -> None:
        """Bump updated_at to now."""
        self.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# MetaAgent
# ---------------------------------------------------------------------------

@AGENT.register_module(force=True)
class MetaAgent(Agent):
    """Orchestrator: decomposes a user task, dispatches sub-agents concurrently,
    reacts to results, and drives self-evolution when agents underperform."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="meta_agent")
    description: str = Field(
        default="Orchestrator that decomposes tasks, dispatches sub-agents concurrently, "
                "reacts to results, and triggers self-evolution when agents underperform."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=False)

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        max_steps: int = 50,
        require_grad: bool = False,
        **kwargs,
    ):
        """Resolve the project root path in addition to base Agent setup."""
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "meta_agent",
            memory_name=memory_name,
            max_steps=max_steps,
            require_grad=require_grad,
            **kwargs,
        )
        from pathlib import Path
        self.project_root = str(Path(__file__).resolve().parents[3])

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def _run(
        self,
        task: str,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> AgentResponse:
        """Run the full orchestration loop for a single user task and return the final answer."""
        logger.info(f"| 🧠 MetaAgent starting: {task}")

        session_id = make_id()
        state = MetaState(session_id=session_id, user_task=task, user_files=files or [])

        # Populate read-once caches (PROJECT.md and agent catalog don't change mid-run).
        try:
            with open(os.path.join(self.project_root, "PROJECT.md"), "r", encoding="utf-8") as f:
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
        state._available_agents = "\n".join(agent_lines) if agent_lines else "[No sub-agents registered]"

        # Agent.__call__ always routes through runtime_manager.invoke, so
        # current_ref() is set to meta's own ref. Sub-agent events flow into
        # state._meta_ref._inbox; escalation hook uses parent_ref to find it.
        state._meta_ref = runtime_manager.current_ref()
        if state._meta_ref is None:
            raise RuntimeError(
                "MetaAgent._run called outside a runtime pump — "
                "invoke via meta_agent(task=...) or runtime_manager.invoke()."
            )

        hook_ctx = AgentContext(id=session_id, agent_name=self.name)
        await hook_manager(
            hook_ctx, 
            HookEvent.ON_START,
            agent_name=self.name,
            extra={
                "task_id": session_id, 
                "task": task,
                "memory_name": self.memory_name, 
                "use_memory": self.use_memory
            },
        )
        trace_manager.emit(agent_start_event(
            session_id=session_id, task_id=session_id,
            agent_name=self.name, task_content=task,
        ))
        t0 = time.monotonic()

        try:
            final_answer = await self._orchestrate(state, ctx=hook_ctx)
            success = True
        except Exception as exc:
            logger.error(f"| ❌ MetaAgent fatal error: {exc}", exc_info=True)
            final_answer = str(exc)
            success = False
        finally:
            await self._cancel_running(state)

        if self.memory_name:
            await hook_manager(
                AgentContext(id=session_id), 
                HookEvent.ON_CALL,
                agent_name=self.name,
                extra={
                    "final_result": final_answer,
                    "success": success
                }
            )

        await hook_manager(
            hook_ctx,
            HookEvent.ON_STOP,
            agent_name=self.name,
            extra={
                "task_id": session_id, 
                "result": final_answer,
                "memory_name": self.memory_name, 
                "use_memory": self.use_memory
            },
        )
        trace_manager.emit(agent_end_event(
            session_id=session_id, task_id=session_id,
            agent_name=self.name, success=success, result=final_answer,
            duration_ms=(time.monotonic() - t0) * 1000,
        ))

        memory_path = ""
        if self.memory_name:
            try:
                info = await memory_manager.get_info(self.memory_name)
                if info and info.instance:
                    memory_path = os.path.join(info.instance.base_dir, f"{session_id}.memory.html")
            except Exception:
                pass

        logger.info(f"| ✅ MetaAgent done (success={success})")
        return AgentResponse(
            success=success,
            message=final_answer or "",
            extra=AgentExtra(data={
                "session_id": session_id,
                "memory_path": memory_path,
                "state": state.model_dump(),
            }),
        )

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def _orchestrate(self, state: MetaState, ctx: Optional[AgentContext] = None) -> str:
        """Loop: produce a plan, dispatch one round at a time, re-plan at each round boundary.

        The LLM is consulted only when there is a decision to make — a blocked
        sub-agent that needs a reply, or a completed round that needs the next
        one (re)planned and dispatched. Individual task completions that don't
        finish the round are just recorded; they don't spend an LLM call.
        """
        await self._record(state.session_id, 0, "start", "START", "requesting initial plan", "running")

        # Step 0: LLM produces the initial plan and dispatches its first round.
        await self._think_and_act(state, events=[], step=0, ctx=ctx)

        # Guard: if nothing was dispatched and no answer was set, avoid a _drain deadlock.
        if not state.has_pending_or_running() and not state.final_answer:
            logger.warning("| ⚠️ Step 0 produced no dispatchable tasks — stopping early.")
            return self._join_results(state)

        step = 0
        while not state.final_answer:
            step += 1      # increment before recording so events share the step with their LLM call
            events = await self._drain(state)

            for e in events:
                if isinstance(e, SubtaskDoneMessage):
                    rec = state.subtask_records.get(e.task_id)
                    if rec:
                        rec.mark_done(e.result)
                    await self._record(
                        state.session_id, step, "subtask_done",
                        f"DONE {e.task_id}", e.result, "done",
                        {"subtask_id": e.task_id, "agent_name": e.agent_name},
                    )
                    logger.info(f"| ✅ Subtask done: {e.task_id}")
                elif isinstance(e, SubtaskFailedMessage):
                    err = e.error or "unknown error"
                    rec = state.subtask_records.get(e.task_id)
                    if rec:
                        rec.mark_failed(err)
                    await self._record(
                        state.session_id, step, "subtask_failed",
                        f"FAILED {e.task_id}", err, "failed",
                        {"subtask_id": e.task_id, "agent_name": e.agent_name},
                    )
                    logger.warning(f"| ❌ Subtask failed: {e.task_id} — {err}")
                elif isinstance(e, EscalationMessage):
                    state._pending_escalations[e.task_id] = e
                    await self._record(
                        state.session_id, step, "escalation",
                        f"ESCALATE {e.task_id} ({e.agent_name})",
                        e.text, "running",
                        {"task_id": e.task_id, "agent_name": e.agent_name, "message": e.text},
                    )

            state.refresh_round_statuses()
            state.touch()

            # Think only at a round boundary (current round complete → re-plan +
            # dispatch next) or when a sub-agent is blocked (reply now).
            at_boundary = state.active_round() is None
            if state._pending_escalations or at_boundary:
                if step > self.max_steps:
                    logger.warning(f"| 🛑 Reached max steps ({self.max_steps})")
                    break
                await self._think_and_act(state, events, step=step, ctx=ctx)

        return state.final_answer or self._join_results(state)

    # ------------------------------------------------------------------
    # Inbox draining
    # ------------------------------------------------------------------

    async def _drain(self, state: MetaState) -> List[BaseMessage]:
        """Block until at least one inbox event arrives, then collect the batch window."""
        first = await state._meta_ref._inbox.get()
        events: List[BaseMessage] = [first]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + _BATCH_WINDOW_S
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                events.append(await asyncio.wait_for(state._meta_ref._inbox.get(), timeout=remaining))
            except asyncio.TimeoutError:
                break
        return events

    # ------------------------------------------------------------------
    # Think + Act: ask LLM and apply its decision
    # ------------------------------------------------------------------

    async def _think_and_act(
        self, state: MetaState, events: List[BaseMessage],
        step: int = 0, ctx: Optional[AgentContext] = None,
    ) -> None:
        """Ask the LLM what to do, then act.

        Always: reply to any blocked sub-agents. At a round boundary (no round
        in flight): apply the plan update, dispatch the next round, or finalize.
        Mid-round (a round is still running): only escalations are actionable —
        the ``plan`` field and dispatch are deferred to the next boundary.
        """
        situation = self._format_situation(state, events)
        messages = await self._get_messages(state=state, situation=situation, ctx=ctx)
        response = await model_manager(
            model=self.model_name, messages=messages, response_format=MetaReactOutput,
        )
        if response.extra and response.extra.parsed_model:
            react = response.extra.parsed_model
        else:
            try:
                react = MetaReactOutput.model_validate_json(response.message)
            except Exception:
                raise RuntimeError(f"LLM structured output failed: {response.message}")
        logger.info(f"| 🤔 React decision: {react.decision}")

        # Reply to blocked sub-agents every turn (explicit reply or default fallback).
        await self._resolve_escalations(state, react, step)

        # Mid-round: a round is still in flight; defer (re)planning and dispatch.
        if state.active_round() is not None:
            state.touch()
            return

        # ---- Round boundary ----
        if react.decision == "stop":
            state.final_answer = react.final_answer or self._join_results(state)
            state.touch()
            return  # _cancel_running is handled centrally in _run's finally block

        # continue: reconcile the full-snapshot plan (if provided) and dispatch the next round.
        await self._dispatch(state, react.plan, step)

        # Nothing running and nothing left to dispatch → synthesize the answer.
        if not state.has_pending_or_running() and not state.final_answer:
            state.final_answer = react.final_answer or self._join_results(state)

        state.touch()

    async def _resolve_escalations(
        self, state: MetaState, react: MetaReactOutput, step: int,
    ) -> None:
        """Resolve every pending escalation with the LLM's reply, or a default fallback."""
        explicit = {er.task_id: er.reply for er in react.escalation_replies}
        _DEFAULT = "No specific guidance available. Use your best judgement or stop gracefully."
        for tid in list(state._pending_escalations.keys()):
            e = state._pending_escalations.pop(tid)
            if not (e.reply_future and not e.reply_future.done()):
                continue
            reply = explicit.get(tid, _DEFAULT)
            e.reply_future.set_result(reply)
            if tid in explicit:
                logger.info(f"| 💬 Escalation resolved: {tid}")
                label, detail = f"REPLY {tid}", reply
            else:
                label, detail = f"REPLY {tid} (default)", "no guidance"
            await self._record(
                state.session_id, step, "escalation_reply",
                label, detail, "", {"task_id": tid, "reply": reply},
            )

    # ------------------------------------------------------------------
    # Plan reconciliation + dispatch
    # ------------------------------------------------------------------

    async def _dispatch(
        self, state: MetaState, react_plan: List[PlanRound], step: int,
    ) -> None:
        """Replace pending rounds with the LLM's new plan (if any), then start the next round.

        Reconciliation:
          - Running and done rounds are frozen — they are never touched.
          - When react_plan is non-empty, all currently pending (not-yet-started) rounds
            are dropped and replaced by the new rounds from the LLM.
          - When react_plan is empty, pending rounds are left unchanged.
          - The LLM outputs only NEW rounds; it never re-emits history.

        Dispatch:
          - After reconciliation, the first pending round is started immediately
            (all its tasks launched in parallel as asyncio Tasks).
        """
        # ── Reconcile (skipped when LLM gave no plan update) ───────────
        if react_plan:
            frozen_count = sum(1 for r in state.plan if r.status in (TaskStatus.RUNNING, TaskStatus.DONE))

            # Drop pending rounds and their task records.
            for rec in state.plan[frozen_count:]:
                for tid in rec.task_ids:
                    state.subtask_records.pop(tid, None)
            state.plan = state.plan[:frozen_count]

            # Append new rounds (LLM outputs only new pending rounds, not frozen history).
            for round_spec in react_plan:
                round_no = len(state.plan) + 1
                rec = PlanRoundRecord(goal=round_spec.goal)
                for task_spec in round_spec.tasks:
                    spec = SubTaskSpec(
                        category=task_spec.category,
                        name=task_spec.name,
                        input=SubTaskInput(
                            task=task_spec.task,
                            files=task_spec.files,
                            target_name=task_spec.target_name,
                        ),
                    )
                    state.subtask_records[spec.id] = SubTaskRecord(spec=spec)
                    rec.task_ids.append(spec.id)
                    await self._record(
                        state.session_id, step, "subtask_planned",
                        f"PLANNED {spec.id}", f"{spec.input.task} → {spec.name}", "pending",
                        {"subtask_id": spec.id, "agent_name": spec.name,
                         "category": spec.category.value, "round": round_no,
                         "task": spec.input.task,
                         "round_label": round_spec.goal or f"Round {round_no}"},
                    )
                if not rec.task_ids:
                    logger.warning(f"| ⚠️ Round {round_no} has no tasks — skipped.")
                    continue  # don't append; avoids vacuous all() → instant DONE loop
                state.plan.append(rec)

        # ── Dispatch ───────────────────────────────────────────────────
        rnd = state.next_pending_round()
        if rnd is None:
            return
        rnd.status = TaskStatus.RUNNING
        for tid in rnd.task_ids:
            record = state.subtask_records[tid]
            record.mark_running(make_id())
            state._running_tasks[tid] = asyncio.create_task(
                self._run_subtask(record, state),
                name=f"subtask-{tid}",
            )
            await self._record(
                state.session_id, step, "subtask_dispatch",
                f"DISPATCH {tid}", f"{record.spec.name}: {record.spec.input.task}", "running",
                {"subtask_id": tid, "agent_name": record.spec.name},
            )
            logger.info(f"| 🚀 Dispatched '{record.spec.input.task}' → {record.spec.name} [{tid}]")

    async def _run_subtask(self, record: SubTaskRecord, state: MetaState) -> None:
        """Run one sub-agent through runtime; post a Subtask{Done,Failed}Message.

        Spawns with parent_ref=meta_ref so the sub-agent's escalation hook can
        ``runtime.ask(parent_ref, EscalationMessage(...))`` directly into meta's
        inbox without any scratch-state plumbing.
        """
        task_id = record.spec.id
        session_id = record.session_id or record.spec.id
        agent_name = record.spec.name

        ctx = AgentContext(
            id=session_id,
            agent_name=agent_name,
            task_id=task_id,
            parent_agent=self.name,
            work_dir=self.base_dir,
            extra={
                "parent_session_id": state.session_id,
                "subtask_id": task_id,
            },
        )
        existing_files = [f for f in (record.spec.input.files or []) if os.path.exists(f)]
        extra_kwargs = {}
        if record.spec.input.target_name is not None:
            extra_kwargs["target_name"] = record.spec.input.target_name

        try:
            sub_agent = await agent_manager.get(agent_name)
            if sub_agent is None:
                raise ValueError(f"No registered agent named {agent_name!r}")
            response = await runtime_manager.invoke(
                sub_agent,
                name=session_id,
                parent_ref=state._meta_ref,
                task=record.spec.input.task,
                files=existing_files or None,
                ctx=ctx,
                **extra_kwargs,
            )
            await state._meta_ref._inbox.put(SubtaskDoneMessage(
                task_id=task_id,
                agent_name=agent_name,
                session_id=session_id,
                result=response.message,
            ))
        except asyncio.CancelledError:
            logger.info(f"| ✋ Subtask {task_id} cancelled")
        except Exception as exc:
            logger.error(f"| ❌ Subtask {task_id} failed: {exc}", exc_info=True)
            await state._meta_ref._inbox.put(SubtaskFailedMessage(
                task_id=task_id,
                agent_name=agent_name,
                session_id=session_id,
                error=str(exc),
            ))

    # ------------------------------------------------------------------
    # Situation & prompt assembly
    # ------------------------------------------------------------------

    def _format_situation(self, state: MetaState, events: List[BaseMessage]) -> str:
        """Render the events plus the round-by-round plan status for the LLM prompt."""
        lines: List[str] = []

        # Show provided files once (they don't change across steps).
        existing = [f for f in state.user_files if os.path.exists(f)]
        if existing:
            lines.append("### Provided Files")
            for f in existing:
                lines.append(f"- {f}")

        if not events and not state.plan:
            lines.append("No events yet. Produce the initial plan as a list of rounds.")
            return "\n".join(lines)

        escalations = [e for e in events if isinstance(e, EscalationMessage)]
        completions = [e for e in events if isinstance(e, (SubtaskDoneMessage, SubtaskFailedMessage))]
        if escalations:
            lines.append("### Escalations Requiring Reply")
            for e in escalations:
                lines.append(f"- ESCALATE [{e.task_id}] ({e.agent_name}): {e.text}")
        if completions:
            lines.append("### Recent Events")
            for e in completions:
                if isinstance(e, SubtaskDoneMessage):
                    lines.append(f"- DONE [{e.task_id}]: {e.result}")
                else:
                    lines.append(f"- FAILED [{e.task_id}]: {e.error}")

        lines.append("\n### Plan Status (rounds run in order; tasks within a round run concurrently)")
        if not state.plan:
            lines.append("(no plan yet)")
        for i, rec in enumerate(state.plan, 1):
            goal = f" — {rec.goal}" if rec.goal else ""
            lines.append(f"**Round {i}** [{state.round_display_status(rec).upper()}]{goal}")
            for tid in rec.task_ids:
                r = state.subtask_records.get(tid)
                if not r:
                    continue
                line = f"  - [{r.status.value.upper()}] {tid} ({r.spec.name}): {r.spec.input.task}"
                if r.result:
                    line += f" → {r.result}"
                if r.error:
                    line += f" ✗ {r.error}"
                lines.append(line)
        return "\n".join(lines)

    async def _get_messages(
        self,
        state: MetaState,
        situation: str,
        ctx: Optional[AgentContext] = None,
    ) -> list:
        """Assemble the full message list for the LLM.

        Reads FileSystemMemory.get() (returns the full HTML of the session file)
        and injects it as ``memory_context`` alongside
        the project context, available agents catalog, and current situation.
        PRE_MESSAGES hooks run last and may further modify the message list.
        """
        # Memory snapshot (read fresh each call — reflects latest plan state).
        memory_context = ""
        if self.memory_name:
            try:
                info = await memory_manager.get_info(self.memory_name)
                if info and info.instance:
                    memory_context = await info.instance.get(session_id=state.session_id) or ""
            except Exception:
                pass

        agent_context = "\n\n".join([
            f"### Task\n{state.user_task}",
            f"### Available Sub-Agents\n{state._available_agents}",
            f"### Current Situation\n{situation}",
            f"### Execution State\n{memory_context or '[No state recorded yet.]'}",
        ])

        work_dir = str(ctx.work_dir if ctx and ctx.work_dir else self.base_dir)
        messages = await prompt_manager.get_messages(
            prompt_name=self.prompt_name,
            system_modules=dict(
                project_root=self.project_root,
                project_context=state._project_context,
                work_dir=work_dir,
            ),
            agent_modules=dict(
                agent_context=agent_context,
            ),
        )

        if ctx is not None:
            hook_ctx = HookContext(
                event=HookEvent.PRE_MESSAGES,
                id=ctx.id,
                agent_name=self.name,
                messages=messages,
                max_tokens=getattr(config, "max_tokens", 0),
            )
            result = await hook_manager(hook_ctx)
            messages = result.modified_messages if result.modified_messages is not None else messages
            if result.additional_context:
                from src.message import SystemMessage
                messages = list(messages) + [SystemMessage(content=result.additional_context)]

        return messages

    # ------------------------------------------------------------------
    # Memory & trace logging
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
        """Write a history entry to the HTML memory file and emit a trace event.

        For subtask lifecycle actions, also passes a ``subtask_event`` payload so
        FileSystemMemory can maintain todos and the flow chart purely event-driven,
        without a separate snapshot push (_sync_state).
        """
        if self.memory_name:
            extra: Dict[str, Any] = {"note": {"event": label, "detail": detail, "status": status}}
            if action in _SUBTASK_EVENTS and data:
                extra["subtask_event"] = {"action": action, "data": data}
            await hook_manager(AgentContext(id=session_id), HookEvent.ON_CALL,
                               agent_name=self.name,
                               extra=extra)
        trace_manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=session_id,
            agent_name=self.name,
            step_number=step,
            action_name=action,
            label=label,
            input=data or {},
        ))

    def _join_results(self, state: MetaState) -> str:
        """Concatenate results from all DONE subtasks as a fallback final answer."""
        return "\n\n".join(
            f"[{r.spec.input.task}]\n{r.result}"
            for r in state.done() if r.result
        ) or "Task completed."

    async def _cancel_running(self, state: MetaState) -> None:
        """Cancel all in-flight asyncio subtask Tasks, wait for them, then mark records CANCELLED."""
        for tid, t in list(state._running_tasks.items()):
            if not t.done():
                t.cancel()
                logger.info(f"| 🛑 Cancelled subtask {tid}")
        await asyncio.gather(*state._running_tasks.values(), return_exceptions=True)
        for rec in state.subtask_records.values():
            if rec.status == TaskStatus.RUNNING:
                rec.mark_cancelled()
                await self._record(
                    state.session_id, 0, "subtask_cancelled",
                    f"CANCELLED {rec.spec.id}", "", "cancelled",
                    {"subtask_id": rec.spec.id, "agent_name": rec.spec.name},
                )
