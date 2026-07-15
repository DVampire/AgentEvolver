"""Reply hook — the reply end of the escalation protocol (mirror of ``escalation_hook``).

An orchestrator's ``reply_tool`` fires this hook to ``runtime.resume`` a sub-agent that is
suspended (blocked on ``escalate_tool`` → ``escalation_hook`` → ``runtime.suspend``). The
pause/resume rendezvous is the general runtime channel, keyed by the sub-agent's task_id;
this hook is just its resume trigger.
"""

from __future__ import annotations

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK
from src.runtime import runtime_manager


@HOOK.register_module(force=True)
class ReplyHook(Hook):
    """Reply end: resume the suspended sub-agent so it continues with the parent's guidance."""

    name:        str = "reply_hook"
    description: str = "Delivers a parent's guidance to a sub-agent blocked on escalation."
    priority:    int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        inp = ctx.input or {}
        task_id = inp.get("task_id") or ""
        reply = inp.get("reply") or ""
        delivered = runtime_manager.resume(task_id, reply)
        if delivered:
            logger.info(f"| 💬 Reply delivered to sub-agent [{task_id}]")
        else:
            logger.warning(f"| ⚠️ ReplyHook: no sub-agent waiting for [{task_id}] (already replied / timed out)")
        return HookResult(decision="allow", additional_context=("delivered" if delivered else "no-waiter"))
