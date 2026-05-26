"""MemoryHook — feeds TraceEvents into GeneralMemorySystem from agent lifecycle hooks.

Mirrors the same hook points as TraceHook but routes events to the memory
layer instead of the trace writer.  Registered once at startup via
hook_manager.initialize(); no per-agent wiring needed.
"""

from __future__ import annotations

from src.hook.types import Hook, HookContext, HookEvent, HookResult
from src.registry import HOOK
from src.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_start_event,
    agent_end_event,
    step_end_event,
    action_end_event,
)


@HOOK.register_module(force=True)
class MemoryHook(Hook):
    """Routes agent lifecycle TraceEvents into the per-session memory system."""

    name: str = "memory_hook"
    description: str = "Feeds agent lifecycle events into GeneralMemorySystem for short-term and working memory."
    events: list = []   # handle all events (same as TraceHook)
    priority: int = 5   # after TraceHook (priority=1), before others

    async def handle(self, ctx: HookContext) -> HookResult:
        from src.memory import memory_manager
        from src.logger import logger

        # Resolve the memory system instance (name comes from agent config or default)
        memory_name = (ctx.extra or {}).get("memory_name", "general_memory_system")
        use_memory = (ctx.extra or {}).get("use_memory", True)
        if not use_memory:
            return HookResult.allow()

        # Build the TraceEvent for this hook point (reuse TraceHook helpers)
        event: TraceEvent | None = self._build_event(ctx)
        if event is None:
            return HookResult.allow()

        # ON_CUSTOM events carry structured plan-state data (todo_update, flowchart_update,
        # history_entry, final_result) and go exclusively to file_system_memory.
        if ctx.event == HookEvent.ON_CUSTOM:
            try:
                fs_info = await memory_manager.get_info("file_system_memory")
                if fs_info and fs_info.instance is not None:
                    extra = ctx.extra or {}
                    meta_type = extra.get("meta_type", "")
                    data = {k: v for k, v in extra.items()
                            if k not in ("meta_type", "memory_name", "use_memory", "_session_state")}
                    _type_map = {
                        "plan_init":   TraceEventType.PLAN_INIT,
                        "plan_update": TraceEventType.PLAN_UPDATE,
                    }
                    ev_type = _type_map.get(meta_type, TraceEventType.CUSTOM)
                    metadata = data if ev_type != TraceEventType.CUSTOM else {"type": meta_type, **data}
                    await fs_info.instance.emit(
                        TraceEvent(
                            event_type=ev_type,
                            session_id=ctx.id,
                            agent_name=ctx.agent_name,
                            metadata=metadata,
                        ),
                        session_id=ctx.id,
                    )
            except Exception as e:
                logger.warning(f"| ⚠️ MemoryHook (ON_CUSTOM) error: {e}")
            return HookResult.allow()

        # Emit into primary memory
        try:
            memory_info = await memory_manager.get_info(memory_name)
            if memory_info and memory_info.instance is not None:
                await memory_info.instance.emit(event, session_id=ctx.id)
        except Exception as e:
            logger.warning(f"| ⚠️ MemoryHook (primary) error on {ctx.event}: {e}")

        # Emit into FileSystemMemory as a secondary sink so every agent's execution
        # is recorded as HTML — runs concurrently with the primary emit above.
        if memory_name != "file_system_memory":
            try:
                fs_info = await memory_manager.get_info("file_system_memory")
                if fs_info and fs_info.instance is not None:
                    await fs_info.instance.emit(event, session_id=ctx.id)
            except Exception as e:
                logger.warning(f"| ⚠️ MemoryHook (file_system) error on {ctx.event}: {e}")

        return HookResult.allow()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_event(self, ctx: HookContext) -> TraceEvent | None:
        task_id = (ctx.extra or {}).get("task_id", ctx.id)

        if ctx.event == HookEvent.ON_START:
            return agent_start_event(
                session_id=ctx.id,
                task_id=task_id,
                agent_name=ctx.agent_name,
                task_content=(ctx.extra or {}).get("task", ""),
            )

        if ctx.event == HookEvent.ON_STOP:
            extra = ctx.extra or {}
            return agent_end_event(
                session_id=ctx.id,
                task_id=task_id,
                agent_name=ctx.agent_name,
                success=not bool(extra.get("error")),
                result=extra.get("result"),
            )

        if ctx.event == HookEvent.POST_STEP:
            step = ctx.step_number or 0
            return step_end_event(
                session_id=ctx.id,
                task_id=task_id,
                agent_name=ctx.agent_name,
                step_number=step,
                thinking=(ctx.extra or {}).get("thinking"),
                next_goal=(ctx.extra or {}).get("next_goal"),
            )

        if ctx.event == HookEvent.POST_ACTION:
            action = ctx.action or {}
            step = ctx.step_number or 0
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            success = not bool((ctx.extra or {}).get("error"))
            error = (ctx.extra or {}).get("error")
            return action_end_event(
                session_id=ctx.id,
                task_id=task_id,
                agent_name=ctx.agent_name,
                step_number=step,
                action_index=idx,
                action_type=atype,
                action_name=aname,
                result=ctx.action_result,
                success=success,
                duration_ms=None,
                error=error,
            )

        return None
