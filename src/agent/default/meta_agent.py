"""MetaAgent — long-lived Actor that orchestrates sub-agents to complete a user task.

Architecture
------------
* MetaAgent is stateless across calls.  Each ``__call__`` creates an isolated
  ``MetaState`` (inbox, escalation futures, running tasks) — concurrent user
  requests never share state.
* Sub-agents are ordinary async coroutines.  They escalate to Meta by placing a
  ``SUBTASK_ESCALATE`` event on ``MetaState.inbox`` with an attached
  ``asyncio.Future``; they suspend on that Future until Meta resolves it.
* The event loop separates escalations (high-priority, immediate reply) from
  completion events (batched with a 100 ms window before one LLM REACT call).
* Completion flow: all USER subtasks terminal → one final LLM synthesis call
  → ``state.final_answer`` set.
* A ``MetaPlanFile`` is written to disk throughout execution — the LLM reads it
  as ``plan_context`` on every REACT call so it can make informed decisions.

Session naming
--------------
* Each ``__call__`` owns a top-level ``session_id`` (``new_session_id()``).
* Each subtask execution gets ``subtask_session_id(session_id, task_id, step)``
  so memory and trace records never bleed between retries or concurrent runs.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.agent.types import Agent, AgentContext, AgentResponse, AgentExtra
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.logger import logger
from src.memory import memory_manager, EventType
from src.model import model_manager
from src.prompt import prompt_manager
from src.registry import AGENT
from src.session import SessionContext
from src.task.types import TaskStatus, SubTaskCategory
from src.trace.server import trace_manager
from src.trace.types import TraceEvent, TraceEventType, agent_start_event, agent_end_event
from src.utils.name_utils import new_session_id, new_task_id, subtask_session_id

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

class SubTaskSpec(BaseModel):
    id: str = Field(default_factory=new_task_id)
    description: str
    agent_name: str
    category: SubTaskCategory = SubTaskCategory.USER
    depends_on: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


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

class MetaReactOutput(BaseModel):
    thinking: str
    decision: Literal["continue", "wait", "stop", "reply_escalation"]
    next_subtasks: List[SubTaskSpec] = Field(default_factory=list)
    final_answer: str = ""
    escalation_task_id: str = ""
    escalation_reply: str = ""
    request_evolution: bool = False
    evolution_target_agent: str = ""


# ---------------------------------------------------------------------------
# MetaPlanFile — lightweight execution log written to disk
# ---------------------------------------------------------------------------

class MetaPlanFile:
    """Append-only execution log for one MetaAgent invocation.

    Structure
    ---------
    ## Subtasks          — live table, updated on every status change
    ## Log               — append-only timestamped event lines
    ## Result            — written once on completion
    """

    def __init__(self, path: str, task: str, session_id: str) -> None:
        self.path = path
        self.task = task
        self.session_id = session_id
        self._records: Dict[str, SubTaskRecord] = {}   # kept in sync via update()
        self._log: List[str] = []
        self._result: Optional[str] = None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def update(self, records: Dict[str, SubTaskRecord]) -> None:
        """Sync subtask table with current MetaState.subtask_records."""
        self._records = dict(records)

    def log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")

    def finalize(self, result: str) -> None:
        self._result = result
        self.log("DONE — final answer written")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self) -> str:
        parts: List[str] = [
            f"# Plan\n",
            f"Task: {self.task}\n",
            f"Session: `{self.session_id}`\n",
            "",
            self._render_subtasks(),
            "",
            self._render_log(),
        ]
        if self._result is not None:
            parts += ["", "## Result\n", self._result]
        return "\n".join(parts)

    def _render_subtasks(self) -> str:
        lines = ["## Subtasks\n"]
        if not self._records:
            lines.append("*(no subtasks yet)*")
            return "\n".join(lines)
        lines.append("| # | Agent | Cat | Status | Description |")
        lines.append("|---|-------|-----|--------|-------------|")
        for i, r in enumerate(self._records.values(), 1):
            emoji = _STATUS_EMOJI.get(r.status, "?")
            cat = "user" if r.spec.category == SubTaskCategory.USER else "evol"
            desc = r.spec.description[:60]
            short_id = r.spec.id.split("_")[-1]  # last 8-char fragment
            lines.append(
                f"| {i} | `{r.spec.agent_name}` | {cat} "
                f"| {emoji} {r.status.value} | {desc} `[{short_id}]` |"
            )
            if r.result:
                lines.append(f"|   |       |     |        | ↳ {r.result[:80]} |")
            elif r.error:
                lines.append(f"|   |       |     |        | ↳ ❌ {r.error[:80]} |")
        return "\n".join(lines)

    def _render_log(self) -> str:
        lines = ["## Log\n"]
        if not self._log:
            lines.append("*(empty)*")
        else:
            lines.extend(self._log)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        content = self.render()
        await asyncio.to_thread(self._write, content)

    def _write(self, content: str) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(content)


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

    # Transient concurrency state — not serialised.
    inbox: asyncio.Queue = Field(default_factory=asyncio.Queue)
    running_tasks: Dict[str, asyncio.Task] = Field(default_factory=dict)
    pending_escalations: Dict[str, "MetaEvent"] = Field(default_factory=dict)

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
        user = [r for r in self.subtask_records.values() if r.spec.category == SubTaskCategory.USER]
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
        workdir: str,
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
            workdir=workdir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "meta_agent",
            memory_name=memory_name,
            max_steps=max_steps,
            require_grad=require_grad,
            use_todo=False,
            **kwargs,
        )
        self.evolution_score_threshold = evolution_score_threshold

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
        logger.info(f"| 🧠 MetaAgent starting: {task[:80]}")

        session_id = new_session_id()
        state = MetaState(session_id=session_id, user_task=task)

        plan_path = os.path.join(self.workdir, f"{session_id}.plan.md")
        plan = MetaPlanFile(path=plan_path, task=task, session_id=session_id)

        if self.use_memory and self.memory_name:
            mem_ctx = SessionContext(id=session_id)
            await memory_manager.start_session(memory_name=self.memory_name, ctx=mem_ctx)
            await memory_manager.add_event(
                memory_name=self.memory_name,
                step_number=0,
                event_type=EventType.TASK_START,
                data={"task": task},
                agent_name=self.name,
                task_id=session_id,
                ctx=mem_ctx,
            )

        hook_ctx = AgentContext(id=session_id, agent_name=self.name)
        await hook_manager(
            hook_ctx, HookEvent.ON_START,
            agent_name=self.name,
            extra={"task_id": session_id, "task": task},
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
            final_answer = await self._run(state, plan)
            success = True
        except Exception as exc:
            logger.error(f"| ❌ MetaAgent fatal error: {exc}", exc_info=True)
            final_answer = str(exc)
            success = False
        finally:
            await self._cancel_running(state)

        plan.finalize(final_answer)
        await plan.save()

        if self.use_memory and self.memory_name:
            mem_ctx = SessionContext(id=session_id)
            await memory_manager.add_event(
                memory_name=self.memory_name,
                step_number=len(state.subtask_records),
                event_type=EventType.TASK_END,
                data={"result": final_answer, "success": success},
                agent_name=self.name,
                task_id=session_id,
                ctx=mem_ctx,
            )
            await memory_manager.end_session(memory_name=self.memory_name, ctx=mem_ctx)

        await hook_manager(
            hook_ctx, HookEvent.ON_STOP,
            agent_name=self.name,
            extra={"task_id": session_id, "result": final_answer},
        )
        trace_manager.emit(agent_end_event(
            session_id=session_id,
            task_id=session_id,
            agent_name=self.name,
            success=success,
            result=final_answer,
            duration_ms=(_time.monotonic() - _t0) * 1000,
        ))

        logger.info(f"| ✅ MetaAgent done (success={success})")
        return AgentResponse(
            success=success,
            message=final_answer or "",
            extra=AgentExtra(data={
                "session_id": session_id,
                "plan_path": plan_path,
                "state": state.model_dump(),
            }),
        )

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def _run(self, state: MetaState, plan: MetaPlanFile) -> str:
        # Cold-start: no subtasks yet — first REACT call produces the initial plan.
        plan.log("START — requesting initial plan")
        await plan.save()

        react = await self._react(state, plan, events=[], step=0)
        await self._apply_react(state, plan, react, step=0)

        step = 0
        while step < self.max_steps:
            step += 1

            escalations, completions = await self._drain(state)

            # Escalations are high-priority: handle immediately without batching.
            if escalations:
                await self._reply_escalations(state, plan, escalations, step=step)

            # Apply completion events to records and sync plan table.
            for event in completions:
                self._apply(state, plan, event)

            if completions:
                plan.update(state.subtask_records)
                await plan.save()

            # Dispatch any subtasks newly unblocked by completed dependencies.
            self._dispatch(state, plan)

            if not completions:
                continue

            if state.is_complete():
                # Final synthesis: LLM reads full plan and writes the answer.
                react = await self._react(state, plan, completions, step=step)
                state.final_answer = react.final_answer or self._join_results(state)
                if react.request_evolution and react.evolution_target_agent:
                    self._submit_evolution(react.evolution_target_agent, state.session_id)
                break

            react = await self._react(state, plan, completions, step=step)
            await self._apply_react(state, plan, react, step=step)

            if state.final_answer:
                break

        return state.final_answer or self._join_results(state)

    # ------------------------------------------------------------------
    # Inbox draining
    # ------------------------------------------------------------------

    async def _drain(
        self, state: MetaState
    ) -> tuple[List[MetaEvent], List[MetaEvent]]:
        first = await state.inbox.get()
        events: List[MetaEvent] = [first]

        deadline = asyncio.get_event_loop().time() + _BATCH_WINDOW_S
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                events.append(await asyncio.wait_for(state.inbox.get(), timeout=remaining))
            except asyncio.TimeoutError:
                break

        escalations = [e for e in events if e.event_type == MetaEventType.SUBTASK_ESCALATE]
        completions = [e for e in events if e.event_type != MetaEventType.SUBTASK_ESCALATE]
        return escalations, completions

    # ------------------------------------------------------------------
    # Escalation: immediate reply
    # ------------------------------------------------------------------

    async def _reply_escalations(
        self, state: MetaState, plan: MetaPlanFile, escalations: List[MetaEvent],
        step: int = 0,
    ) -> None:
        for e in escalations:
            state.pending_escalations[e.task_id] = e
            plan.log(f"ESCALATE {e.task_id[:8]} ({e.agent_name}) — {(e.escalation_message or '')[:80]}")
            self._emit(state.session_id, step, "escalation", f"Escalation from {e.agent_name}", {
                "task_id": e.task_id,
                "agent_name": e.agent_name,
                "message": e.escalation_message,
            })

        plan.update(state.subtask_records)
        await plan.save()

        messages = await self._build_messages(
            task=state.user_task,
            available_agents=await self._agents_info(),
            situation=self._fmt_escalations(escalations),
            plan_context=plan.render(),
        )
        react = await self._llm_react(messages)
        logger.info(f"| 🤔 Escalation decision: {react.decision}")

        if react.decision == "reply_escalation":
            self._resolve_escalation(state, react.escalation_task_id, react.escalation_reply)
            plan.log(f"REPLY {react.escalation_task_id[:8]} — {react.escalation_reply[:80]}")
            self._emit(state.session_id, step, "escalation_reply",
                       f"Reply → {react.escalation_task_id[:8]}", {
                "task_id": react.escalation_task_id,
                "reply": react.escalation_reply,
            })
        else:
            for e in escalations:
                self._resolve_escalation(
                    state, e.task_id,
                    "No specific guidance available. Use your best judgement or stop gracefully.",
                )
                plan.log(f"REPLY {e.task_id[:8]} — (default: no guidance)")
                self._emit(state.session_id, step, "escalation_reply",
                           f"Default reply → {e.task_id[:8]}", {"task_id": e.task_id})

        if react.request_evolution and react.evolution_target_agent:
            self._submit_evolution(react.evolution_target_agent, state.session_id)

        await plan.save()
        state.touch()

    def _resolve_escalation(self, state: MetaState, task_id: str, reply: str) -> None:
        e = state.pending_escalations.pop(task_id, None)
        if e and e.reply_future and not e.reply_future.done():
            e.reply_future.set_result(reply)
            logger.info(f"| 💬 Escalation resolved: {task_id}")
            return
        # Fallback: resolve the first unresolved pending escalation.
        for tid, e in list(state.pending_escalations.items()):
            if e.reply_future and not e.reply_future.done():
                e.reply_future.set_result(reply)
                del state.pending_escalations[tid]
                logger.warning(f"| 💬 Escalation fallback resolved: {tid}")
                return

    # ------------------------------------------------------------------
    # React
    # ------------------------------------------------------------------

    async def _react(
        self, state: MetaState, plan: MetaPlanFile, events: List[MetaEvent],
        step: int = 0,
    ) -> MetaReactOutput:
        import time as _time
        situation = self._fmt_situation(state, events) if events else "No events yet. Produce the initial plan."
        messages = await self._build_messages(
            task=state.user_task,
            available_agents=await self._agents_info(),
            situation=situation,
            plan_context=plan.render(),
        )
        self._emit(state.session_id, step, "llm_start", f"React step {step}", {
            "message_count": len(messages),
        })
        t0 = _time.monotonic()
        output = await self._llm_react(messages)
        elapsed = (_time.monotonic() - t0) * 1000
        self._emit(state.session_id, step, "llm_end", f"React step {step} → {output.decision}", {
            "decision": output.decision,
            "thinking": output.thinking[:200],
            "duration_ms": elapsed,
        })
        return output

    async def _apply_react(
        self, state: MetaState, plan: MetaPlanFile, react: MetaReactOutput,
        step: int = 0,
    ) -> None:
        logger.info(f"| 🤔 React decision: {react.decision}")

        if react.decision == "continue":
            for spec in react.next_subtasks:
                state.subtask_records[spec.id] = SubTaskRecord(spec=spec)
                plan.log(f"PLANNED {spec.id[:8]} — {spec.description[:60]} → {spec.agent_name}")
                self._emit(state.session_id, step, "subtask_planned",
                           f"Plan: {spec.description[:50]} → {spec.agent_name}", {
                    "subtask_id": spec.id,
                    "agent_name": spec.agent_name,
                    "category": spec.category.value,
                    "depends_on": spec.depends_on,
                    "description": spec.description,
                })
            plan.update(state.subtask_records)
            self._dispatch(state, plan)

        elif react.decision == "stop":
            state.final_answer = react.final_answer
            self._emit(state.session_id, step, "stop",
                       "Final answer ready", {"answer": react.final_answer[:200]})
            await self._cancel_running(state)

        elif react.decision == "reply_escalation":
            self._resolve_escalation(state, react.escalation_task_id, react.escalation_reply)
            plan.log(f"REPLY {react.escalation_task_id[:8]} — {react.escalation_reply[:80]}")

        if react.request_evolution and react.evolution_target_agent:
            self._emit(state.session_id, step, "evolution_requested",
                       f"Evolution → {react.evolution_target_agent}", {
                "target_agent": react.evolution_target_agent,
            })
            self._submit_evolution(react.evolution_target_agent, state.session_id)

        await plan.save()
        state.touch()

    # ------------------------------------------------------------------
    # Sub-task dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, state: MetaState, plan: MetaPlanFile) -> None:
        for record in state.ready():
            sid = subtask_session_id(state.session_id, record.spec.id, record.step)
            record.mark_running(sid)
            t = asyncio.create_task(
                self._run_subtask(record, state),
                name=f"subtask-{record.spec.id}",
            )
            state.running_tasks[record.spec.id] = t
            plan.log(f"DISPATCH {record.spec.id[:8]} → {record.spec.agent_name} [{sid[-12:]}]")
            self._emit(state.session_id, 0, "subtask_dispatch",
                       f"Dispatch → {record.spec.agent_name}: {record.spec.description[:50]}", {
                "subtask_id": record.spec.id,
                "subtask_session_id": sid,
                "agent_name": record.spec.agent_name,
                "category": record.spec.category.value,
                "step": record.step,
                "description": record.spec.description,
            })
            logger.info(
                f"| 🚀 Dispatched '{record.spec.description[:60]}' "
                f"→ {record.spec.agent_name} [{sid}]"
            )

    async def _run_subtask(self, record: SubTaskRecord, state: MetaState) -> None:
        from src.agent.context import agent_manager

        task_id = record.spec.id
        session_id = record.session_id or subtask_session_id(
            state.session_id, task_id, record.step
        )
        agent_name = record.spec.agent_name

        try:
            sub_agent = await agent_manager.get(agent_name)

            # Write escalation config into the subtask's HookSessionState.scratch.
            # EscalationHook (global singleton) reads it from ctx.extra["_session_state"].
            # Per-session isolation is guaranteed by HookContextManager.
            session_state = await hook_manager.context.get_or_create(session_id)
            session_state.scratch["escalation"] = {
                "meta_inbox": state.inbox,
                "reply_future": None,   # filled fresh on each escalation by the hook
                "task_id": task_id,
                "agent_name": agent_name,
            }

            ctx = AgentContext(
                id=session_id,
                agent_name=agent_name,
                task_id=task_id,
                parent_agent=self.name,
                extra={
                    "parent_session_id": state.session_id,
                    "subtask_id": task_id,
                },
            )

            response = await sub_agent(
                task=record.spec.description,
                files=record.spec.files or None,
                ctx=ctx,
            )

            await state.inbox.put(MetaEvent(
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
            await state.inbox.put(MetaEvent(
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

    async def _build_messages(
        self,
        task: str,
        available_agents: str,
        situation: str = "",
        plan_context: str = "",
    ) -> list:
        return await prompt_manager.get_messages(
            prompt_name=self.prompt_name,
            system_modules=dict(available_agents=available_agents),
            agent_modules=dict(task=task, situation=situation, plan_context=plan_context),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply(self, state: MetaState, plan: MetaPlanFile, event: MetaEvent) -> None:
        record = state.subtask_records.get(event.task_id)
        if record is None:
            return
        if event.event_type == MetaEventType.SUBTASK_DONE:
            record.mark_done(event.result or "")
            plan.log(f"DONE {event.task_id[:8]} — {(event.result or '')[:80]}")
            self._emit(state.session_id, 0, "subtask_done",
                       f"Done: {record.spec.description[:50]}", {
                "subtask_id": event.task_id,
                "agent_name": event.agent_name,
                "result": (event.result or "")[:300],
            })
            logger.info(f"| ✅ Subtask done: {event.task_id}")
        elif event.event_type == MetaEventType.SUBTASK_FAILED:
            record.mark_failed(event.error or "unknown error")
            plan.log(f"FAILED {event.task_id[:8]} — {event.error or 'unknown'}")
            self._emit(state.session_id, 0, "subtask_failed",
                       f"Failed: {record.spec.description[:50]}", {
                "subtask_id": event.task_id,
                "agent_name": event.agent_name,
                "error": event.error,
            })
            logger.warning(f"| ❌ Subtask failed: {event.task_id} — {event.error}")
        state.touch()

    def _emit(
        self,
        session_id: str,
        step: int,
        action_name: str,
        label: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Fire-and-forget a CUSTOM TraceEvent for a MetaAgent lifecycle point."""
        trace_manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=session_id,
            task_id=session_id,
            agent_name=self.name,
            step_number=step,
            action_name=action_name,
            label=label,
            input=data or {},
        ))

    async def _agents_info(self) -> str:
        from src.agent.context import agent_manager
        configs = await agent_manager.list()
        lines = [f"- {c.name}: {c.description}" for c in configs if c.name != self.name]
        return "\n".join(lines) if lines else "[No sub-agents registered]"

    def _fmt_situation(self, state: MetaState, events: List[MetaEvent]) -> str:
        lines = ["### Recent Events"]
        for e in events:
            if e.event_type == MetaEventType.SUBTASK_DONE:
                lines.append(f"- DONE [{e.task_id}]: {(e.result or '')[:200]}")
            elif e.event_type == MetaEventType.SUBTASK_FAILED:
                lines.append(f"- FAILED [{e.task_id}]: {e.error}")
        lines.append("\n### Subtask Status")
        for r in state.subtask_records.values():
            line = f"- [{r.status.value.upper()}] {r.spec.id}: {r.spec.description[:80]}"
            if r.result:
                line += f" → {r.result[:100]}"
            lines.append(line)
        return "\n".join(lines)

    def _fmt_escalations(self, escalations: List[MetaEvent]) -> str:
        lines = ["### Escalations Requiring Reply"]
        for e in escalations:
            lines.append(f"- ESCALATE [{e.task_id}] ({e.agent_name}): {e.escalation_message}")
        return "\n".join(lines)

    def _join_results(self, state: MetaState) -> str:
        return "\n\n".join(
            f"[{r.spec.description}]\n{r.result}"
            for r in state.done() if r.result
        ) or "Task completed."

    async def _cancel_running(self, state: MetaState) -> None:
        for tid, t in list(state.running_tasks.items()):
            if not t.done():
                t.cancel()
                logger.info(f"| 🛑 Cancelled subtask {tid}")
        if state.running_tasks:
            await asyncio.gather(*state.running_tasks.values(), return_exceptions=True)

    def _submit_evolution(self, agent_name: str, trigger_session_id: str) -> None:
        asyncio.create_task(self._do_evolution(agent_name, trigger_session_id))

    async def _do_evolution(self, agent_name: str, trigger_session_id: str) -> None:
        try:
            from src.task.server import task_manager, TaskCategory
            from src.task.types import TaskPriority
            task_id = await task_manager.submit(
                content=f"Evolve agent: {agent_name}",
                category=TaskCategory.EVOLVER,
                priority=TaskPriority.LOW,
                entity_key=f"agent/{agent_name}",
                metadata={"trigger_session_id": trigger_session_id},
            )
            logger.info(f"| 🧬 Evolution task submitted for '{agent_name}': {task_id}")
        except Exception as exc:
            logger.warning(f"| ⚠️  Failed to submit evolution task: {exc}")
