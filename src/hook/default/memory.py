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

        inp = ctx.input
        if inp is None:
            return HookResult.allow()

        memory_name = inp.memory_name or "general_memory_system"
        if not inp.use_memory:
            return HookResult.allow()

        # ON_CALL: always route directly to FileSystemMemory as an AGENT_CALL
        # with the caller's payload as metadata.
        if inp.event == HookEvent.ON_CALL:
            try:
                fs_info = await memory_manager.get_info("file_system_memory")
                if fs_info and fs_info.instance is not None:
                    data = inp.model_dump(exclude={"memory_name", "use_memory"}, exclude_none=True)
                    await fs_info.instance.emit(
                        TraceEvent(
                            event_type=TraceEventType.AGENT_CALL,
                            session_id=ctx.id,
                            agent_name=inp.agent_name,
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
            logger.warning(f"| ⚠️ MemoryHook (primary) error on {inp.event}: {e}")

        # FileSystemMemory as secondary sink
        if memory_name != "file_system_memory":
            try:
                fs_info = await memory_manager.get_info("file_system_memory")
                if fs_info and fs_info.instance is not None:
                    await fs_info.instance.emit(event, session_id=ctx.id)
            except Exception as e:
                logger.warning(f"| ⚠️ MemoryHook (file_system) error on {inp.event}: {e}")

        return HookResult.allow()

    def _build_event(self, ctx: HookContext) -> TraceEvent | None:
        inp = ctx.input
        if inp is None:
            return None
        task_id = inp.task_id or ctx.id
        agent_name = inp.agent_name

        if inp.event == HookEvent.ON_START:
            return agent_start_event(
                session_id=ctx.id, task_id=task_id,
                agent_name=agent_name,
                task_content=inp.task,
            )

        if inp.event == HookEvent.ON_STOP:
            return agent_end_event(
                session_id=ctx.id, task_id=task_id,
                agent_name=agent_name,
                success=not bool(inp.error),
                result=inp.result,
            )

        if inp.event == HookEvent.POST_STEP:
            return agent_call_event(
                session_id=ctx.id, task_id=task_id,
                agent_name=agent_name,
                step_number=inp.step_number,
                thinking=inp.thinking,
                next_goal=inp.next_goal,
                memory=inp.memory,
            )

        if inp.event == HookEvent.POST_ACTION:
            action = inp.action or {}
            idx = action.get("index", 0)
            atype = action.get("type", "tool")
            aname = action.get("name", "")
            description = action.get("description") or None
            factory = tool_call_event if atype == "tool" else skill_call_event
            return factory(
                session_id=ctx.id, task_id=task_id,
                agent_name=agent_name,
                step_number=inp.step_number, action_index=idx, action_name=aname,
                result=inp.action_result, success=not bool(inp.error),
                duration_ms=None, error=inp.error,
                description=description,
            )

        return None
