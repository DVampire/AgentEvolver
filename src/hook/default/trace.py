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

        event: Optional[TraceEvent] = None

        agent_name = (ctx.extra or {}).get("agent_name", "")

        if ctx.extra.get("event") == HookEvent.ON_START:
            event = agent_start_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                task_content=self._task_content(ctx),
            )
            parent_session_id = (ctx.extra or {}).get("parent_session_id")
            subtask_id = (ctx.extra or {}).get("subtask_id")
            if parent_session_id:
                event = event.model_copy(update={"metadata": {
                    **event.metadata,
                    "parent_session_id": parent_session_id,
                    "subtask_id": subtask_id or "",
                }})
            self._timers[f"{ctx.id}:agent"] = time.monotonic()

        elif ctx.extra.get("event") == HookEvent.ON_STOP:
            elapsed = self._pop_timer(f"{ctx.id}:agent")
            event = agent_end_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                success=True,
                result=None,
                duration_ms=elapsed,
            )

        elif ctx.extra.get("event") == HookEvent.PRE_STEP:
            step = ctx.extra.get("step_number") or 0
            self._timers[f"{ctx.id}:step:{step}"] = time.monotonic()

        elif ctx.extra.get("event") == HookEvent.POST_STEP:
            step = ctx.extra.get("step_number") or 0
            elapsed = self._pop_timer(f"{ctx.id}:step:{step}")
            event = agent_call_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                thinking=(ctx.extra or {}).get("thinking"),
                next_goal=(ctx.extra or {}).get("next_goal"),
                duration_ms=elapsed,
            )

        elif ctx.extra.get("event") == HookEvent.PRE_ACTION:
            action = ctx.extra.get("action") or {}
            step = ctx.extra.get("step_number") or 0
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

        elif ctx.extra.get("event") == HookEvent.POST_ACTION:
            action = ctx.extra.get("action") or {}
            step = ctx.extra.get("step_number") or 0
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            success = not bool((ctx.extra or {}).get("error"))
            error = (ctx.extra or {}).get("error")
            elapsed = self._pop_timer(f"{ctx.id}:action:{step}:{idx}")
            factory = tool_call_event if atype == "tool" else skill_call_event
            event = factory(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=agent_name,
                step_number=step,
                action_index=idx,
                action_name=aname,
                result=ctx.extra.get("action_result"),
                success=success,
                duration_ms=elapsed,
                error=error,
            )

        if event is not None:
            await trace_manager.emit(event)

        return HookResult.allow()

    def _task_id(self, ctx: HookContext) -> str:
        return (ctx.extra or {}).get("task_id", ctx.id)

    def _task_content(self, ctx: HookContext) -> str:
        return (ctx.extra or {}).get("task", "")

    def _pop_timer(self, key: str) -> Optional[float]:
        start = self._timers.pop(key, None)
        return None if start is None else (time.monotonic() - start) * 1000
