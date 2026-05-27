"""MemoryHook — feeds TraceEvents into the memory systems from agent lifecycle hooks."""

from __future__ import annotations

from src.hook.types import Hook, HookContext, HookEvent, HookResult
from src.registry import HOOK
from src.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_start_event,
    agent_call_event,
    agent_end_event,
    tool_call_event,
    skill_call_event,
)


@HOOK.register_module(force=True)
class MemoryHook(Hook):
    """Routes agent lifecycle TraceEvents into the per-session memory system."""

    name: str = "memory_hook"
    description: str = "Feeds agent lifecycle events into memory systems."
    events: list = []
    priority: int = 5

    async def handle(self, ctx: HookContext) -> HookResult:
        from src.memory import memory_manager
        from src.logger import logger

        memory_name = (ctx.extra or {}).get("memory_name", "general_memory_system")
        use_memory = (ctx.extra or {}).get("use_memory", True)
        if not use_memory:
            return HookResult.allow()

        # ON_CALL: always route directly to FileSystemMemory as an AGENT_CALL
        # with the caller's payload as metadata.
        if ctx.event == HookEvent.ON_CALL:
            try:
                fs_info = await memory_manager.get_info("file_system_memory")
                if fs_info and fs_info.instance is not None:
                    extra = ctx.extra or {}
                    data = {k: v for k, v in extra.items()
                            if k not in ("memory_name", "use_memory", "_session_state")}
                    await fs_info.instance.emit(
                        TraceEvent(
                            event_type=TraceEventType.AGENT_CALL,
                            session_id=ctx.id,
                            agent_name=ctx.agent_name,
                            metadata=data,
                        ),
                        session_id=ctx.id,
                    )
            except Exception as e:
                logger.warning(f"| ⚠️ MemoryHook (ON_CALL) error: {e}")
            return HookResult.allow()

        event: TraceEvent | None = self._build_event(ctx)
        if event is None:
            return HookResult.allow()

        # Emit into primary memory
        try:
            memory_info = await memory_manager.get_info(memory_name)
            if memory_info and memory_info.instance is not None:
                await memory_info.instance.emit(event, session_id=ctx.id)
        except Exception as e:
            logger.warning(f"| ⚠️ MemoryHook (primary) error on {ctx.event}: {e}")

        # FileSystemMemory as secondary sink
        if memory_name != "file_system_memory":
            try:
                fs_info = await memory_manager.get_info("file_system_memory")
                if fs_info and fs_info.instance is not None:
                    await fs_info.instance.emit(event, session_id=ctx.id)
            except Exception as e:
                logger.warning(f"| ⚠️ MemoryHook (file_system) error on {ctx.event}: {e}")

        return HookResult.allow()

    def _build_event(self, ctx: HookContext) -> TraceEvent | None:
        task_id = (ctx.extra or {}).get("task_id", ctx.id)

        if ctx.event == HookEvent.ON_START:
            return agent_start_event(
                session_id=ctx.id, task_id=task_id,
                agent_name=ctx.agent_name,
                task_content=(ctx.extra or {}).get("task", ""),
            )

        if ctx.event == HookEvent.ON_STOP:
            extra = ctx.extra or {}
            return agent_end_event(
                session_id=ctx.id, task_id=task_id,
                agent_name=ctx.agent_name,
                success=not bool(extra.get("error")),
                result=extra.get("result"),
            )

        if ctx.event == HookEvent.POST_STEP:
            step = ctx.step_number or 0
            return agent_call_event(
                session_id=ctx.id, task_id=task_id,
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
            description = action.get("description") or None
            success = not bool((ctx.extra or {}).get("error"))
            error = (ctx.extra or {}).get("error")
            factory = tool_call_event if atype == "tool" else skill_call_event
            return factory(
                session_id=ctx.id, task_id=task_id,
                agent_name=ctx.agent_name,
                step_number=step, action_index=idx, action_name=aname,
                result=ctx.action_result, success=success,
                duration_ms=None, error=error,
                description=description,
            )

        return None
