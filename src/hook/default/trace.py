"""TraceHook — probe middleware that emits TraceEvents from agent lifecycle hooks.

Translates HookEvents into typed TraceEvents and fire-and-forgets them to
TraceManager.  The agent execution loop is never blocked or slowed.

Register once at startup::

    from src.hook.default.trace import TraceHook
    from src.hook import hook_manager

    hook_manager.register(TraceHook())
"""

from __future__ import annotations

import json
import time
from typing import Dict, Optional

from pydantic import PrivateAttr

from src.hook.types import Hook, HookContext, HookEvent, HookResult
from src.trace.types import (
    TraceEvent,
    action_end_event,
    action_start_event,
    agent_end_event,
    agent_start_event,
    step_end_event,
    step_start_event,
)


class TraceHook(Hook):
    """Fire-and-forget probe: translates HookEvents → TraceEvents."""

    name: str = "trace_hook"
    description: str = "Emits structured TraceEvents for every agent lifecycle hook."
    events: list = []   # empty = handle all events
    priority: int = 1   # run first so timing is accurate

    # Key: "<session_id>:<scope_key>" — per-instance via PrivateAttr.
    _timers: Dict[str, float] = PrivateAttr(default_factory=dict)

    async def handle(self, ctx: HookContext) -> HookResult:
        # Lazy import to avoid circular deps at module load time.
        from src.trace.server import trace_manager

        event: Optional[TraceEvent] = None

        if ctx.event == HookEvent.ON_START:
            event = agent_start_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=ctx.agent_name,
                task_content=self._task_content(ctx),
            )
            # If this agent was dispatched by a MetaAgent, carry the linkage
            # in metadata so the frontend can nest this session under the parent.
            parent_session_id = (ctx.extra or {}).get("parent_session_id")
            subtask_id = (ctx.extra or {}).get("subtask_id")
            if parent_session_id:
                event = event.model_copy(update={"metadata": {
                    **event.metadata,
                    "parent_session_id": parent_session_id,
                    "subtask_id": subtask_id or "",
                }})
            self._timers[f"{ctx.id}:agent"] = time.monotonic()

        elif ctx.event == HookEvent.ON_STOP:
            elapsed = self._pop_timer(f"{ctx.id}:agent")
            event = agent_end_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=ctx.agent_name,
                success=True,
                result=None,
                duration_ms=elapsed,
            )

        elif ctx.event == HookEvent.PRE_STEP:
            step = ctx.step_number or 0
            event = step_start_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=ctx.agent_name,
                step_number=step,
            )
            self._timers[f"{ctx.id}:step:{step}"] = time.monotonic()

        elif ctx.event == HookEvent.POST_STEP:
            step = ctx.step_number or 0
            elapsed = self._pop_timer(f"{ctx.id}:step:{step}")
            thinking = (ctx.extra or {}).get("thinking")
            next_goal = (ctx.extra or {}).get("next_goal")
            event = step_end_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=ctx.agent_name,
                step_number=step,
                thinking=thinking,
                next_goal=next_goal,
                duration_ms=elapsed,
            )

        elif ctx.event == HookEvent.PRE_ACTION:
            action = ctx.action or {}
            step = ctx.step_number or 0
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            aargs = action.get("args_parsed", action.get("args", {}))
            if isinstance(aargs, str):
                try:
                    aargs = json.loads(aargs)
                except Exception:
                    aargs = {"raw": aargs}
            event = action_start_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=ctx.agent_name,
                step_number=step,
                action_index=idx,
                action_type=atype,
                action_name=aname,
                action_args=aargs,
            )
            self._timers[f"{ctx.id}:action:{step}:{idx}"] = time.monotonic()

        elif ctx.event == HookEvent.POST_ACTION:
            action = ctx.action or {}
            step = ctx.step_number or 0
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            result = ctx.action_result
            success = not bool((ctx.extra or {}).get("error"))
            error = (ctx.extra or {}).get("error")
            elapsed = self._pop_timer(f"{ctx.id}:action:{step}:{idx}")
            event = action_end_event(
                session_id=ctx.id,
                task_id=self._task_id(ctx),
                agent_name=ctx.agent_name,
                step_number=step,
                action_index=idx,
                action_type=atype,
                action_name=aname,
                result=result,
                success=success,
                duration_ms=elapsed,
                error=error,
            )

        if event is not None:
            trace_manager.emit(event)

        return HookResult.allow()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _task_id(self, ctx: HookContext) -> str:
        return (ctx.extra or {}).get("task_id", ctx.id)

    def _task_content(self, ctx: HookContext) -> str:
        return (ctx.extra or {}).get("task", "")

    def _pop_timer(self, key: str) -> Optional[float]:
        start = self._timers.pop(key, None)
        if start is None:
            return None
        return (time.monotonic() - start) * 1000
