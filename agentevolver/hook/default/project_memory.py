"""Learn durable project facts from successful, already-settled runtime evidence."""

from __future__ import annotations

import asyncio

from agentevolver.hook.types import Hook, HookContext, HookEvent, HookResult
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class ProjectMemoryHook(Hook):
    """Project Trace outcomes into cross-session memory without an LLM summarizer."""

    name: str = "project_memory_hook"
    description: str = "Learns verified commands and recurring failures across sessions."
    priority: int = 190
    events: list = [HookEvent.TASK_COMPLETED]

    async def handle(self, ctx: HookContext) -> HookResult:
        if (ctx.input or {}).get("event") != HookEvent.TASK_COMPLETED:
            return HookResult.allow()
        try:
            from agentevolver.config import config
            from agentevolver.memory.project import ProjectMemoryStore
            from agentevolver.session import resolve_workspace_root
            from agentevolver.trace import trace_manager

            workspace = resolve_workspace_root(
                ctx, str(getattr(config, "workspace_root", "") or ""),
            )
            source = (ctx.extra or {}).get("source_workspace")
            store = ProjectMemoryStore(workspace, source_workspace=source)
            events = list(trace_manager.events(ctx.id) or [])
            await asyncio.to_thread(store.learn_trace, events, session_id=ctx.id)
        except Exception:
            # Auto-memory is an evidence projection, never part of task success.
            return HookResult.allow()
        return HookResult.allow()


__all__ = ["ProjectMemoryHook"]
