"""MetaAgent — orchestrator that decomposes a user task and dispatches sub-agents concurrently.

Execution model
---------------
  user task
      │
      ▼
  _think_and_act()  ──LLM──►  plan (SubTaskSpec list + dependency graph) ──► _dispatch()
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
                    →  _apply_react sets reply_future.set_result(reply)
  (any escalation the LLM ignored gets a default fallback reply)
  sub-agent's ask unblocks, hook returns the guidance, sub-agent resumes.

Memory & observability
----------------------
  Write  (all paths via MemoryHook):
    lifecycle events  →  MemoryHook  →  GeneralMemorySystem (working memory)
                      →  MemoryHook  →  FileSystemMemory    (HTML per session)
    plan-state updates (todo/flowchart/history)
                      →  hook ON_CALL  →  MemoryHook  →  FileSystemMemory

  Read:
    meta_agent._get_messages() reads FileSystemMemory and injects HTML
                               as ``memory_context`` on every _think_and_act() call.
    sub-agents._get_agent_context() reads GeneralMemorySystem and injects
                                    a text summary into each step's prompt.

Task ID / HTML naming
---------------------
  Each agent run has its own task_id (= AgentContext.id) which also names its HTML file.
  meta_agent : make_id()      → {id}.memory.html  (root run)
  sub-agent  : SubTaskSpec.id → {id}.memory.html  (one file per subtask)
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


# ---------------------------------------------------------------------------
# Sub-task spec & record
# ---------------------------------------------------------------------------

class SubTaskInput(BaseModel):
    task: str
    files: List[str] = Field(default_factory=list)
    target_name: Optional[str] = Field(default=None, description="Name of the tool/agent to optimize or evaluate.")
    extra: Dict[str, Any] = Field(default_factory=dict)


class SubTaskSpec(BaseModel):
    id: str = Field(default_factory=lambda: make_id())
    category: SubTaskCategory = SubTaskCategory.ACTOR
    name: str = Field(description="Agent name to dispatch this subtask to.")
    input: SubTaskInput
    depends_on: List[str] = Field(default_factory=list)


class SubTaskRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    spec: SubTaskSpec
    status: TaskStatus = TaskStatus.PENDING
    step: int = Field(default=1)
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
    decision: Literal["continue", "wait", "stop"]
    tasks: List[SubTaskSpec] = Field(default_factory=list)
    escalation_replies: List[EscalationReply] = Field(default_factory=list)
    final_answer: str = ""


# ---------------------------------------------------------------------------
# Per-invocation state
# ---------------------------------------------------------------------------

class MetaState(BaseModel):
    """Complete, isolated state for one MetaAgent invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    user_task: str
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

    def pending(self) -> List[SubTaskRecord]:
        """Return all subtasks currently in PENDING status."""
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.PENDING]

    def done(self) -> List[SubTaskRecord]:
        """Return all subtasks that completed successfully."""
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.DONE]

    def ready(self) -> List[SubTaskRecord]:
        """Pending subtasks whose dependencies are all terminal (DONE or FAILED)."""
        terminal_ids = {
            r.spec.id for r in self.subtask_records.values()
            if r.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
        }
        return [
            r for r in self.pending()
            if all(dep in terminal_ids for dep in r.spec.depends_on)
        ]

    def is_complete(self) -> bool:
        """True when every ACTOR-category subtask has reached a terminal status."""
        user = [r for r in self.subtask_records.values() if r.spec.category == SubTaskCategory.ACTOR]
        return bool(user) and all(
            r.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
            for r in user
        )

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
        state = MetaState(session_id=session_id, user_task=task)

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
            hook_ctx, HookEvent.ON_START,
            agent_name=self.name,
            extra={"task_id": session_id, "task": task,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
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
            await hook_manager(AgentContext(id=session_id), HookEvent.ON_CALL,
                               agent_name=self.name,
                               extra={"final_result": final_answer, "success": success})

        await hook_manager(
            hook_ctx, HookEvent.ON_STOP,
            agent_name=self.name,
            extra={"task_id": session_id, "result": final_answer,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
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
        """Inner loop: initial think+act → drain inbox → think+act, until final_answer set or max_steps."""
        await self._record(state.session_id, 0, "start", "START", "requesting initial plan", "running")

        await self._think_and_act(state, events=[], step=0, ctx=ctx)

        step = 0
        while step < self.max_steps and not state.final_answer:
            step += 1

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
            state.touch()
            await self._sync_state(state)

            await self._think_and_act(state, events, step=step, ctx=ctx)

        return state.final_answer or self._join_results(state)

    # ------------------------------------------------------------------
    # Inbox draining
    # ------------------------------------------------------------------

    async def _drain(self, state: MetaState) -> List[BaseMessage]:
        """Block until at least one inbox event arrives, then collect the batch window."""
        first = await state._meta_ref._inbox.get()
        events: List[BaseMessage] = [first]

        deadline = asyncio.get_event_loop().time() + _BATCH_WINDOW_S
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
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
        """Ask the LLM what to do next and apply the decision: resolve escalations, register/dispatch tasks, set final answer."""
        # ---- Think: format situation, build prompt, call LLM ----
        if not events and not state.subtask_records:
            situation = "No events yet. Produce the initial plan."
        else:
            lines: List[str] = []
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
            lines.append("\n### Subtask Status")
            for r in state.subtask_records.values():
                line = f"- [{r.status.value.upper()}] {r.spec.id}: {r.spec.input.task}"
                if r.result:
                    line += f" → {r.result}"
                lines.append(line)
            situation = "\n".join(lines)

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

        # ---- Act: resolve every pending escalation (explicit reply or default fallback) ----
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

        # ---- Act: register new tasks / set final / dispatch ----
        if react.decision == "continue":
            for spec in react.tasks:
                state.subtask_records[spec.id] = SubTaskRecord(spec=spec)
                await self._record(
                    state.session_id, step, "subtask_planned",
                    f"PLANNED {spec.id}", f"{spec.input.task} → {spec.name}", "pending",
                    {"subtask_id": spec.id, "agent_name": spec.name,
                     "category": spec.category.value, "depends_on": spec.depends_on},
                )
            await self._dispatch(state)
        elif react.decision == "stop":
            state.final_answer = react.final_answer
            await self._cancel_running(state)
        else:  # "wait"
            await self._dispatch(state)

        # Auto-finalize once every actor subtask has reached a terminal status.
        if state.is_complete() and not state.final_answer:
            state.final_answer = react.final_answer or self._join_results(state)

        state.touch()

    # ------------------------------------------------------------------
    # Sub-task dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, state: MetaState) -> None:
        """Launch asyncio Tasks for every PENDING subtask whose dependencies are satisfied."""
        ready = state.ready()
        for record in ready:
            record.mark_running(make_id())
            state._running_tasks[record.spec.id] = asyncio.create_task(
                self._run_subtask(record, state),
                name=f"subtask-{record.spec.id}",
            )
            await self._record(
                state.session_id, 0, "subtask_dispatch",
                f"DISPATCH {record.spec.id}", f"{record.spec.name}: {record.spec.input.task}", "running",
                {"subtask_id": record.spec.id, "agent_name": record.spec.name, "step": record.step},
            )
            logger.info(f"| 🚀 Dispatched '{record.spec.input.task}' → {record.spec.name} [{record.spec.id}]")
        if ready:
            await self._sync_state(state)

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
        extra_kwargs = dict(record.spec.input.extra)
        if record.spec.input.target_name is not None:
            extra_kwargs["target_name"] = record.spec.input.target_name

        try:
            response = await runtime_manager.invoke(
                agent_name,
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
        finally:
            await hook_manager.end_session(session_id)

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    async def _get_messages(
        self,
        state: MetaState,
        situation: str,
        ctx: Optional[AgentContext] = None,
    ) -> list:
        """Assemble the full message list for the LLM (project md, memory, agents catalog, PRE_MESSAGES hooks)."""
        # PROJECT.md
        try:
            with open(os.path.join(self.project_root, "PROJECT.md"), "r", encoding="utf-8") as f:
                project_context = f.read()
        except Exception:
            project_context = "(PROJECT.md not found)"

        # Memory snapshot
        memory_context = ""
        if self.memory_name:
            try:
                info = await memory_manager.get_info(self.memory_name)
                if info and info.instance:
                    memory_context = await info.instance.get(session_id=state.session_id) or ""
            except Exception:
                pass

        # Available sub-agents
        agent_lines: List[str] = []
        for name in await agent_manager.list():
            if name == self.name:
                continue
            info = await agent_manager.get_info(name)
            if info:
                agent_lines.append(f"- {info.name}: {info.description}")
        available_agents = "\n".join(agent_lines) if agent_lines else "[No sub-agents registered]"

        agent_context = "\n\n".join([
            f"### Task\n{state.user_task}",
            f"### Available Sub-Agents\n{available_agents}",
            f"### Current Situation\n{situation}",
            f"### Execution State\n{memory_context or '[No state recorded yet.]'}",
        ])

        work_dir = str(ctx.work_dir if ctx and ctx.work_dir else self.base_dir)
        messages = await prompt_manager.get_messages(
            prompt_name=self.prompt_name,
            system_modules=dict(
                project_root=self.project_root,
                project_context=project_context,
                work_dir=work_dir,
            ),
            agent_modules=dict(
                agent_context=agent_context,
                examples="",
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
        """Write a history entry to the HTML memory file and emit a trace event."""
        if self.memory_name:
            await hook_manager(AgentContext(id=session_id), HookEvent.ON_CALL,
                               agent_name=self.name,
                               extra={"note": {"event": label, "detail": detail, "status": status}})
        trace_manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=session_id,
            agent_name=self.name,
            step_number=step,
            action_name=action,
            label=label,
            input=data or {},
        ))

    async def _sync_state(self, state: MetaState) -> None:
        """Push current subtask records to FileSystemMemory as a single AGENT_CALL."""
        if not self.memory_name:
            return

        records = state.subtask_records
        rounds: Dict[str, int] = {}

        def _round(tid: str) -> int:
            if tid in rounds:
                return rounds[tid]
            rec = records.get(tid)
            if rec is None or not rec.spec.depends_on:
                rounds[tid] = 1
            else:
                dep_rounds = [_round(d) for d in rec.spec.depends_on if d in records]
                rounds[tid] = (max(dep_rounds) + 1) if dep_rounds else 1
            return rounds[tid]

        for tid in records:
            _round(tid)

        todos = [
            {"id": r.spec.id, "description": r.spec.input.task,
             "agent_name": r.spec.name, "status": r.status.value}
            for r in records.values()
        ]
        flow_steps = [
            {"step": i + 1, "label": r.spec.input.task, "agents": [r.spec.name],
             "status": r.status.value, "round": rounds.get(r.spec.id, 1)}
            for i, r in enumerate(records.values())
        ]
        await hook_manager(
            AgentContext(id=state.session_id), HookEvent.ON_CALL,
            agent_name=self.name,
            extra={"todos": todos, "flow_steps": flow_steps},
        )

    def _join_results(self, state: MetaState) -> str:
        """Concatenate results from all DONE subtasks as a fallback final answer."""
        return "\n\n".join(
            f"[{r.spec.input.task}]\n{r.result}"
            for r in state.done() if r.result
        ) or "Task completed."

    async def _cancel_running(self, state: MetaState) -> None:
        """Cancel all in-flight asyncio subtask Tasks and wait for them to finish."""
        for tid, t in list(state._running_tasks.items()):
            if not t.done():
                t.cancel()
                logger.info(f"| 🛑 Cancelled subtask {tid}")
        await asyncio.gather(*state._running_tasks.values(), return_exceptions=True)
