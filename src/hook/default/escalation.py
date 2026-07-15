"""Escalation hook — the send end of "a blocked sub-agent asks its parent".

``escalate_tool`` fires this hook, which posts an EscalationMessage to the parent's inbox
and ``runtime.suspend``s on the sub-agent's task_id, blocking until the parent answers. The
reply end is the mirror ``reply_hook`` (see ``reply.py``), fired by the parent's
``reply_tool`` to ``runtime.resume`` this sub-agent. The pause/resume rendezvous is a
general runtime primitive (``runtime_manager.suspend`` / ``resume``), not owned by this
protocol — the hooks are just its two triggers.
"""

from __future__ import annotations

import asyncio

from src.hook.types import Hook, HookContext, HookResult
from src.logger import logger
from src.registry import HOOK
from src.runtime import runtime_manager

_ESCALATION_TIMEOUT_S = 300.0


@HOOK.register_module(force=True)
class EscalationHook(Hook):
    """Send end: post the escalation to the parent, then suspend until the parent replies."""

    name:        str = "escalation_hook"
    description: str = "Suspends a sub-agent on escalation and awaits the parent's reply."
    priority:    int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        inp = ctx.input or {}
        parent_session_id = inp.get("parent_session_id")
        if not parent_session_id:
            logger.warning("| ⚠️ EscalationHook: no parent_session_id in ctx.input — nowhere to escalate")
            return HookResult.allow()

        parent_ref = runtime_manager.get(parent_session_id)
        if parent_ref is None:
            logger.warning(f"| ⚠️ EscalationHook: parent ref {parent_session_id!r} not found in runtime")
            return HookResult.allow()

        from src.agent.actor.meta_agent import EscalationMessage  # local: break import cycle

        task_id = inp.get("task_id") or ctx.id
        escalation = EscalationMessage(
            task_id    = task_id,
            agent_name = inp.get("agent_name", ""),
            session_id = ctx.id,
            reason     = inp.get("reason") or "",
            situation  = inp.get("situation") or "",
            suggestion = inp.get("suggestion") or "",
        )

        logger.info(f"| 🆘 Escalation sent from {escalation.agent_name} [{task_id}] → ref {parent_session_id}")
        try:
            await runtime_manager.send(parent_ref, escalation)
            reply: str = await runtime_manager.suspend(task_id, timeout=_ESCALATION_TIMEOUT_S)
        except asyncio.TimeoutError:
            reply = "Meta Agent did not respond in time. Please stop the current subtask gracefully."
            logger.warning(f"| ⏰ Escalation timeout for session {ctx.id}")
        except Exception as e:
            logger.error(f"| ❌ EscalationHook failed to deliver: {e}")
            return HookResult.allow()

        logger.info(f"| 💬 Escalation reply received for session {ctx.id}: {reply}")
        return HookResult(decision="allow", additional_context=f"[Meta Agent Guidance]\n{reply}")
