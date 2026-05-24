"""MetaAgent — orchestrator that decomposes a user task and dispatches sub-agents concurrently.

Execution model
---------------
  user task
      │
      ▼
  _react()  ──LLM──►  plan  (SubTaskSpec list + dependency graph)
      │
      ├─ _dispatch() ──► asyncio.Task per ready subtask  ──► sub-agent(__call__)
      │                        │                                    │
      │               SUBTASK_ESCALATE                      SUBTASK_DONE
      │                        │                                    │
      └──────────── MetaState._inbox (asyncio.Queue) ◄─────────────┘
                               │
                    _drain() collects completions
                    (100 ms batch window)
                               │
                    _react() again with results
                               │
                    all done ──► final LLM synthesis ──► AgentResponse

Escalation flow
---------------
  sub-agent blocks  →  puts SUBTASK_ESCALATE on inbox (with asyncio.Future)
  meta _reply_escalations()  →  LLM decides reply  →  Future.set_result()
  sub-agent resumes

Memory & observability
----------------------
  Write  (all paths via MemoryHook):
    lifecycle events  →  MemoryHook  →  GeneralMemorySystem (working memory)
                      →  MemoryHook  →  FileSystemMemory    (HTML per session)
    plan-state updates (todo/flowchart/history)
                      →  hook ON_CUSTOM  →  MemoryHook  →  FileSystemMemory

  Read:
    meta_agent._get_memory_context()  →  FileSystemMemory.get()
                                    →  HTML injected as ``memory_context`` on every _react()
    sub-agents._get_agent_context() →  GeneralMemorySystem.get()
                                    →  text summary injected into each step's prompt

Task ID / HTML naming
---------------------
  Each agent run has its own task_id (= AgentContext.id) which also names its HTML file.
  meta_agent : make_id()      → {id}.memory.html  (root run)
  sub-agent  : SubTaskSpec.id → {id}.memory.html  (one file per subtask)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.agent.types import Agent, AgentContext, AgentResponse, AgentExtra
from src.config import config
from src.hook.server import hook_manager
from src.hook.types import HookEvent, HookContext
from src.logger import logger
from src.memory import memory_manager
from src.model import model_manager
from src.prompt import prompt_manager
from src.registry import AGENT
from src.task.types import TaskStatus, SubTaskCategory
from src.trace.server import trace_manager
from src.trace.types import TraceEvent, TraceEventType, agent_start_event, agent_end_event
from src.utils.name_utils import make_id

# Collect completion events arriving within this window before one LLM REACT call.
_BATCH_WINDOW_S = 0.1

_STATUS_EMOJI = {
    TaskStatus.PENDING:   "⏳",
    TaskStatus.RUNNING:   "🔄",
    TaskStatus.DONE:      "✅",
    TaskStatus.FAILED:    "❌",
    TaskStatus.CANCELLED: "🚫",
}


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

    def prepare_retry(self) -> None:
        self.step += 1
        self.status = TaskStatus.PENDING
        self.session_id = None
        self.result = None
        self.error = None
        self.started_at = None
        self.finished_at = None


# ---------------------------------------------------------------------------
# Actor messages
# ---------------------------------------------------------------------------

class MetaEventType(str, Enum):
    SUBTASK_DONE     = "subtask_done"
    SUBTASK_FAILED   = "subtask_failed"
    SUBTASK_ESCALATE = "subtask_escalate"


class MetaEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_type: MetaEventType
    task_id: str
    agent_name: str
    session_id: str
    result: Optional[str] = None
    error: Optional[str] = None
    escalation_message: Optional[str] = None
    reply_future: Optional[asyncio.Future] = None


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
    """Complete, isolated state for one MetaAgent.__call__ invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    user_task: str
    subtask_records: Dict[str, SubTaskRecord] = Field(default_factory=dict)
    llm_turns: List[Dict[str, str]] = Field(default_factory=list)
    final_answer: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Transient concurrency state — excluded from model_dump / serialization.
    _inbox: asyncio.Queue = PrivateAttr(default_factory=asyncio.Queue)
    _running_tasks: Dict[str, asyncio.Task] = PrivateAttr(default_factory=dict)
    _pending_escalations: Dict[str, "MetaEvent"] = PrivateAttr(default_factory=dict)

    def pending(self) -> List[SubTaskRecord]:
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.PENDING]

    def running(self) -> List[SubTaskRecord]:
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.RUNNING]

    def done(self) -> List[SubTaskRecord]:
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.DONE]

    def failed(self) -> List[SubTaskRecord]:
        return [r for r in self.subtask_records.values() if r.status == TaskStatus.FAILED]

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
        user = [r for r in self.subtask_records.values() if r.spec.category == SubTaskCategory.ACTOR]
        return bool(user) and all(
            r.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
            for r in user
        )

    def touch(self) -> None:
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
        evolution_score_threshold: float = 0.5,
        require_grad: bool = False,
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
            max_steps=max_steps,
            require_grad=require_grad,
            **kwargs,
        )
        self.evolution_score_threshold = evolution_score_threshold
        from pathlib import Path
        self.project_root = str(Path(__file__).resolve().parents[3])

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> AgentResponse:
        logger.info(f"| 🧠 MetaAgent starting: {task}")

        session_id = make_id()
        state = MetaState(session_id=session_id, user_task=task)

        hook_ctx = AgentContext(id=session_id, agent_name=self.name)
        await hook_manager(
            hook_ctx, HookEvent.ON_START,
            agent_name=self.name,
            extra={"task_id": session_id, "task": task,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )
        trace_manager.emit(agent_start_event(
            session_id=session_id,
            task_id=session_id,
            agent_name=self.name,
            task_content=task,
        ))

        import time as _time
        _t0 = _time.monotonic()

        try:
            final_answer = await self._run(state, ctx=hook_ctx)
            success = True
        except Exception as exc:
            logger.error(f"| ❌ MetaAgent fatal error: {exc}", exc_info=True)
            final_answer = str(exc)
            success = False
        finally:
            await self._cancel_running(state)

        await self._mem_emit(session_id, "final_result", {
            "result": final_answer,
            "success": success,
        })

        await hook_manager(
            hook_ctx, HookEvent.ON_STOP,
            agent_name=self.name,
            extra={"task_id": session_id, "result": final_answer,
                   "memory_name": self.memory_name, "use_memory": self.use_memory},
        )
        trace_manager.emit(agent_end_event(
            session_id=session_id,
            task_id=session_id,
            agent_name=self.name,
            success=success,
            result=final_answer,
            duration_ms=(_time.monotonic() - _t0) * 1000,
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

    async def _run(self, state: MetaState, ctx: Optional[AgentContext] = None) -> str:
        await self._record(state.session_id, 0, "start", "START", "requesting initial plan", "running")

        react = await self._react(state, events=[], step=0, ctx=ctx)
        await self._apply_react(state, react, step=0)

        step = 0
        while step < self.max_steps:
            step += 1

            escalations, completions = await self._drain(state)

            if escalations:
                await self._reply_escalations(state, escalations, step=step, ctx=ctx)

            for event in completions:
                await self._apply(state, event)

            if completions:
                await self._sync_state(state)

            await self._dispatch(state)

            if not completions:
                continue

            if state.is_complete():
                react = await self._react(state, completions, step=step, ctx=ctx)
                state.final_answer = react.final_answer or self._join_results(state)
                break

            react = await self._react(state, completions, step=step, ctx=ctx)
            await self._apply_react(state, react, step=step)

            if state.final_answer:
                break

        return state.final_answer or self._join_results(state)

    # ------------------------------------------------------------------
    # Inbox draining
    # ------------------------------------------------------------------

    async def _drain(
        self, state: MetaState
    ) -> tuple[List[MetaEvent], List[MetaEvent]]:
        first = await state._inbox.get()
        events: List[MetaEvent] = [first]

        deadline = asyncio.get_event_loop().time() + _BATCH_WINDOW_S
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                events.append(await asyncio.wait_for(state._inbox.get(), timeout=remaining))
            except asyncio.TimeoutError:
                break

        escalations = [e for e in events if e.event_type == MetaEventType.SUBTASK_ESCALATE]
        completions = [e for e in events if e.event_type != MetaEventType.SUBTASK_ESCALATE]
        return escalations, completions

    # ------------------------------------------------------------------
    # Escalation: immediate reply
    # ------------------------------------------------------------------

    async def _reply_escalations(
        self, state: MetaState, escalations: List[MetaEvent],
        step: int = 0, ctx: Optional[AgentContext] = None,
    ) -> None:
        for e in escalations:
            state._pending_escalations[e.task_id] = e
            await self._record(
                state.session_id, step, "escalation",
                f"ESCALATE {e.task_id[:8]} ({e.agent_name})",
                e.escalation_message or "", "running",
                {"task_id": e.task_id, "agent_name": e.agent_name, "message": e.escalation_message},
            )

        await self._sync_state(state)

        memory_context = await self._get_memory_context(state)
        messages = await self._get_messages(
            task=state.user_task,
            available_agents=await self._agents_info(),
            situation=self._fmt_escalations(escalations),
            memory_context=memory_context,
            session_id=state.session_id,
            ctx=ctx,
        )
        react = await self._llm_react(messages)
        logger.info(f"| 🤔 Escalation react decision: {react.decision}")

        await self._apply_react(state, react, step=step)

        resolved = {er.task_id for er in react.escalation_replies}
        for e in escalations:
            if e.task_id not in resolved:
                self._resolve_escalation(
                    state, e.task_id,
                    "No specific guidance available. Use your best judgement or stop gracefully.",
                )
                await self._record(
                    state.session_id, step, "escalation_reply",
                    f"REPLY {e.task_id[:8]} (default)", "no guidance",
                    data={"task_id": e.task_id},
                )

        state.touch()

    def _resolve_escalation(self, state: MetaState, task_id: str, reply: str) -> None:
        e = state._pending_escalations.pop(task_id, None)
        if e and e.reply_future and not e.reply_future.done():
            e.reply_future.set_result(reply)
            logger.info(f"| 💬 Escalation resolved: {task_id}")
            return
        for tid, e in list(state._pending_escalations.items()):
            if e.reply_future and not e.reply_future.done():
                e.reply_future.set_result(reply)
                del state._pending_escalations[tid]
                logger.warning(f"| 💬 Escalation fallback resolved: {tid}")
                return

    # ------------------------------------------------------------------
    # React
    # ------------------------------------------------------------------

    async def _react(
        self, state: MetaState, events: List[MetaEvent],
        step: int = 0, ctx: Optional[AgentContext] = None,
    ) -> MetaReactOutput:
        import time as _time
        situation = self._fmt_situation(state, events) if events else "No events yet. Produce the initial plan."
        memory_context = await self._get_memory_context(state)
        messages = await self._get_messages(
            task=state.user_task,
            available_agents=await self._agents_info(),
            situation=situation,
            memory_context=memory_context,
            session_id=state.session_id,
            ctx=ctx,
        )
        trace_manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=state.session_id,
            agent_name=self.name,
            step_number=step,
            action_name="llm_start",
            label=f"React step {step}",
            input={"message_count": len(messages)},
        ))
        t0 = _time.monotonic()
        output = await self._llm_react(messages)
        trace_manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=state.session_id,
            agent_name=self.name,
            step_number=step,
            action_name="llm_end",
            label=f"React step {step} → {output.decision}",
            input={"decision": output.decision, "thinking": output.thinking,
                   "duration_ms": (_time.monotonic() - t0) * 1000},
        ))
        return output

    async def _apply_react(
        self, state: MetaState, react: MetaReactOutput,
        step: int = 0,
    ) -> None:
        logger.info(f"| 🤔 React decision: {react.decision}")

        for er in react.escalation_replies:
            self._resolve_escalation(state, er.task_id, er.reply)
            await self._record(
                state.session_id, step, "escalation_reply",
                f"REPLY {er.task_id[:8]}", er.reply, "",
                {"task_id": er.task_id, "reply": er.reply},
            )

        if react.decision == "continue":
            for spec in react.tasks:
                state.subtask_records[spec.id] = SubTaskRecord(spec=spec)
                await self._record(
                    state.session_id, step, "subtask_planned",
                    f"PLANNED {spec.id[:8]}", f"{spec.input.task} → {spec.name}", "pending",
                    {"subtask_id": spec.id, "agent_name": spec.name,
                     "category": spec.category.value, "depends_on": spec.depends_on},
                )
            await self._sync_state(state)
            await self._dispatch(state)

        elif react.decision == "stop":
            state.final_answer = react.final_answer
            trace_manager.emit(TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=state.session_id,
                agent_name=self.name,
                step_number=step,
                action_name="stop",
                label="Final answer ready",
                input={"answer": react.final_answer},
            ))
            await self._cancel_running(state)

        state.touch()

    # ------------------------------------------------------------------
    # Sub-task dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, state: MetaState) -> None:
        for record in state.ready():
            record.mark_running(record.spec.id)
            t = asyncio.create_task(
                self._run_subtask(record, state),
                name=f"subtask-{record.spec.id}",
            )
            state._running_tasks[record.spec.id] = t
            await self._record(
                state.session_id, 0, "subtask_dispatch",
                f"DISPATCH {record.spec.id[:8]}", f"{record.spec.name}: {record.spec.input.task}", "running",
                {"subtask_id": record.spec.id, "agent_name": record.spec.name, "step": record.step},
            )
            logger.info(
                f"| 🚀 Dispatched '{record.spec.input.task}' "
                f"→ {record.spec.name} [{record.spec.id}]"
            )

    async def _run_subtask(self, record: SubTaskRecord, state: MetaState) -> None:
        from src.agent.server import agent_manager

        task_id = record.spec.id
        session_id = record.session_id or record.spec.id
        agent_name = record.spec.name

        try:
            sub_agent = await agent_manager.get(agent_name)

            session_state = await hook_manager.context.get_or_create(session_id)
            session_state.scratch["escalation"] = {
                "meta_inbox": state._inbox,
                "reply_future": None,
                "task_id": task_id,
                "agent_name": agent_name,
            }

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

            response = await sub_agent(
                task=record.spec.input.task,
                files=existing_files or None,
                ctx=ctx,
                **extra_kwargs,
            )

            await state._inbox.put(MetaEvent(
                event_type=MetaEventType.SUBTASK_DONE,
                task_id=task_id,
                agent_name=agent_name,
                session_id=session_id,
                result=response.message,
            ))

        except asyncio.CancelledError:
            logger.info(f"| ✋ Subtask {task_id} cancelled")
        except Exception as exc:
            logger.error(f"| ❌ Subtask {task_id} failed: {exc}", exc_info=True)
            await state._inbox.put(MetaEvent(
                event_type=MetaEventType.SUBTASK_FAILED,
                task_id=task_id,
                agent_name=agent_name,
                session_id=session_id,
                error=str(exc),
            ))
        finally:
            await hook_manager.end_session(session_id)

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    async def _llm_react(self, messages: list) -> MetaReactOutput:
        response = await model_manager(
            model=self.model_name, messages=messages, response_format=MetaReactOutput,
        )
        return response.extra.parsed_model

    async def _get_messages(
        self,
        task: str,
        available_agents: str,
        situation: str = "",
        memory_context: str = "",
        session_id: str = "",
        ctx: Optional[AgentContext] = None,
    ) -> list:
        agent_context = "\n\n".join([
            f"### Task\n{task}",
            f"### Available Sub-Agents\n{available_agents}",
            f"### Current Situation\n{situation}",
            f"### Execution State\n{memory_context or '[No state recorded yet.]'}",
        ])

        project_context = self._load_project_md()
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
    # Helpers
    # ------------------------------------------------------------------

    async def _apply(self, state: MetaState, event: MetaEvent) -> None:
        record = state.subtask_records.get(event.task_id)
        if record is None:
            return
        if event.event_type == MetaEventType.SUBTASK_DONE:
            record.mark_done(event.result or "")
            await self._record(
                state.session_id, 0, "subtask_done",
                f"DONE {event.task_id[:8]}", (event.result or "")[:200], "done",
                {"subtask_id": event.task_id, "agent_name": event.agent_name},
            )
            logger.info(f"| ✅ Subtask done: {event.task_id}")
        elif event.event_type == MetaEventType.SUBTASK_FAILED:
            record.mark_failed(event.error or "unknown error")
            await self._record(
                state.session_id, 0, "subtask_failed",
                f"FAILED {event.task_id[:8]}", event.error or "unknown", "failed",
                {"subtask_id": event.task_id, "agent_name": event.agent_name},
            )
            logger.warning(f"| ❌ Subtask failed: {event.task_id} — {event.error}")
        state.touch()

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
        await self._mem_emit(session_id, "history_entry", {
            "event": label, "detail": detail, "status": status,
        })
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
        """Push current subtask records to FileSystemMemory (todos + flowchart)."""
        todos = [
            {"id": r.spec.id, "description": r.spec.input.task,
             "agent_name": r.spec.name, "status": r.status.value}
            for r in state.subtask_records.values()
        ]
        rounds = self._compute_rounds(state.subtask_records)
        steps = [
            {"step": i + 1, "label": r.spec.input.task, "agents": [r.spec.name],
             "status": r.status.value, "round": rounds.get(r.spec.id, 1)}
            for i, r in enumerate(state.subtask_records.values())
        ]
        await self._mem_emit(state.session_id, "todo_update", {"todos": todos})
        await self._mem_emit(state.session_id, "flowchart_update", {"steps": steps})

    async def _get_memory_context(self, state: MetaState) -> str:
        if not self.memory_name:
            return ""
        try:
            info = await memory_manager.get_info(self.memory_name)
            if info and info.instance:
                return await info.instance.get(session_id=state.session_id) or ""
        except Exception:
            pass
        return ""

    async def _mem_emit(self, session_id: str, meta_type: str, data: Dict[str, Any]) -> None:
        if not self.memory_name:
            return
        ctx = AgentContext(id=session_id)
        await hook_manager(
            ctx,
            HookEvent.ON_CUSTOM,
            agent_name=self.name,
            extra={"meta_type": meta_type, **data},
        )

    @staticmethod
    def _compute_rounds(records: Dict[str, SubTaskRecord]) -> Dict[str, int]:
        rounds: Dict[str, int] = {}

        def _round(task_id: str) -> int:
            if task_id in rounds:
                return rounds[task_id]
            rec = records.get(task_id)
            if rec is None or not rec.spec.depends_on:
                rounds[task_id] = 1
            else:
                dep_rounds = [_round(d) for d in rec.spec.depends_on if d in records]
                rounds[task_id] = (max(dep_rounds) + 1) if dep_rounds else 1
            return rounds[task_id]

        for tid in records:
            _round(tid)
        return rounds

    def _load_project_md(self) -> str:
        path = os.path.join(self.project_root, "PROJECT.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return "(PROJECT.md not found)"

    async def _agents_info(self) -> str:
        from src.agent.server import agent_manager
        names = await agent_manager.list()
        lines = []
        for name in names:
            if name == self.name:
                continue
            info = await agent_manager.get_info(name)
            if info:
                lines.append(f"- {info.name}: {info.description}")
        return "\n".join(lines) if lines else "[No sub-agents registered]"

    def _fmt_situation(self, state: MetaState, events: List[MetaEvent]) -> str:
        lines = ["### Recent Events"]
        for e in events:
            if e.event_type == MetaEventType.SUBTASK_DONE:
                lines.append(f"- DONE [{e.task_id}]: {(e.result or '')}")
            elif e.event_type == MetaEventType.SUBTASK_FAILED:
                lines.append(f"- FAILED [{e.task_id}]: {e.error}")
        lines.append("\n### Subtask Status")
        for r in state.subtask_records.values():
            line = f"- [{r.status.value.upper()}] {r.spec.id}: {r.spec.input.task}"
            if r.result:
                line += f" → {r.result}"
            lines.append(line)
        return "\n".join(lines)

    def _fmt_escalations(self, escalations: List[MetaEvent]) -> str:
        lines = ["### Escalations Requiring Reply"]
        for e in escalations:
            lines.append(f"- ESCALATE [{e.task_id}] ({e.agent_name}): {e.escalation_message}")
        return "\n".join(lines)

    def _join_results(self, state: MetaState) -> str:
        return "\n\n".join(
            f"[{r.spec.input.task}]\n{r.result}"
            for r in state.done() if r.result
        ) or "Task completed."

    async def _cancel_running(self, state: MetaState) -> None:
        for tid, t in list(state._running_tasks.items()):
            if not t.done():
                t.cancel()
                logger.info(f"| 🛑 Cancelled subtask {tid}")
        if state._running_tasks:
            await asyncio.gather(*state._running_tasks.values(), return_exceptions=True)
