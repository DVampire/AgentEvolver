"""TraceHook — probe middleware that emits TraceEvents from agent lifecycle hooks."""

from __future__ import annotations

import json
import time
from typing import Dict, Optional

from pydantic import PrivateAttr

from src.hook.types import Hook, HookContext, HookEvent, HookResult
from src.registry import HOOK
from src.trace.types import (
    TraceEvent,
    agent_start_event,
    agent_call_event,
    agent_end_event,
    tool_start_event,
    tool_call_event,
    skill_start_event,
    skill_call_event,
)


@HOOK.register_module(force=True)
class TraceHook(Hook):
    """Fire-and-forget probe: translates HookEvents → TraceEvents."""

    name: str = "trace_hook"
    description: str = "Emits structured TraceEvents for every agent lifecycle hook."
    events: list = []
    priority: int = 1

    _timers: Dict[str, float] = PrivateAttr(default_factory=dict)

    async def handle(self, ctx: HookContext) -> HookResult:
        from src.trace.server import trace_manager

        inp = ctx.input
        if inp is None:
            return HookResult.allow()

        event: Optional[TraceEvent] = None
        agent_name = inp.agent_name

        if inp.event == HookEvent.ON_START:
            event = agent_start_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                task_content=self._task_content(ctx),
            )
            if inp.parent_session_id:
                event = event.model_copy(update={"metadata": {
                    **event.metadata,
                    "parent_session_id": inp.parent_session_id,
                    "subtask_id": inp.subtask_id or "",
                }})
            self._timers[f"{ctx.id}:agent"] = time.monotonic()

        elif inp.event == HookEvent.ON_STOP:
            elapsed = self._pop_timer(f"{ctx.id}:agent")
            event = agent_end_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                success=True,
                result=None,
                duration_ms=elapsed,
            )

        elif inp.event == HookEvent.PRE_STEP:
            step = inp.step_number
            self._timers[f"{ctx.id}:step:{step}"] = time.monotonic()

        elif inp.event == HookEvent.POST_STEP:
            step = inp.step_number
            elapsed = self._pop_timer(f"{ctx.id}:step:{step}")
            event = agent_call_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                thinking=inp.thinking,
                next_goal=inp.next_goal,
                duration_ms=elapsed,
            )

        elif inp.event == HookEvent.PRE_ACTION:
            action = inp.action or {}
            step = inp.step_number
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            aargs = action.get("args_parsed", action.get("args", {}))
            if isinstance(aargs, str):
                try:
                    aargs = json.loads(aargs)
                except Exception:
                    aargs = {"raw": aargs}
            factory = tool_start_event if atype == "tool" else skill_start_event
            event = factory(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                action_index=idx,
                action_name=aname,
                action_args=aargs,
            )
            self._timers[f"{ctx.id}:action:{step}:{idx}"] = time.monotonic()

        elif inp.event == HookEvent.POST_ACTION:
            action = inp.action or {}
            step = inp.step_number
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            success = not bool(inp.error)
            error = inp.error
            elapsed = self._pop_timer(f"{ctx.id}:action:{step}:{idx}")
            factory = tool_call_event if atype == "tool" else skill_call_event
            event = factory(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                action_index=idx,
                action_name=aname,
                result=inp.action_result,
                success=success,
                duration_ms=elapsed,
                error=error,
            )

        if event is not None:
            await trace_manager.emit(event)

        return HookResult.allow()

    def _task_id(self, ctx: HookContext) -> str:
        if ctx.input is None:
            return ctx.id
        return ctx.input.task_id or ctx.id

    def _task_content(self, ctx: HookContext) -> str:
        if ctx.input is None:
            return ""
        return ctx.input.task

    def _pop_timer(self, key: str) -> Optional[float]:
        start = self._timers.pop(key, None)
        return None if start is None else (time.monotonic() - start) * 1000
