"""EscalationHook — routes ON_ESCALATE to the parent AgentRef via the runtime registry.

Design
------
- parent_session_id (= MetaAgent's ref.name) is injected into ctx.extra by MetaAgent
  when creating the sub-agent's AgentContext.
- The hook looks up the parent AgentRef in runtime_manager._refs by that name, then
  calls runtime_manager.ask(parent_ref, EscalationMessage) and blocks until MetaAgent
  sets the reply_future — no separate session registry needed.
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
    """Suspends a sub-agent on ON_ESCALATE and awaits a reply from MetaAgent."""

    name:        str = "escalation_hook"
    description: str = "Suspends a sub-agent on ON_ESCALATE and awaits MetaAgent reply."
    priority:    int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        extra            = ctx.extra or {}
        parent_session_id = extra.get("parent_session_id")
        if not parent_session_id:
            logger.warning("| ⚠️ EscalationHook: no parent_session_id in ctx.extra — nowhere to escalate")
            return HookResult.allow()

        parent_ref = runtime_manager.get(parent_session_id)
        if parent_ref is None:
            logger.warning(f"| ⚠️ EscalationHook: parent ref {parent_session_id!r} not found in runtime")
            return HookResult.allow()

        from src.agent.actor.meta_agent import EscalationMessage  # local: break import cycle

        escalation = EscalationMessage(
            task_id    = extra.get("task_id", ctx.id),
            agent_name = extra.get("agent_name", ""),
            session_id = ctx.id,
            reason     = extra.get("reason", ""),
            situation  = extra.get("situation", ""),
            suggestion = extra.get("suggestion", ""),
        )

        logger.info(
            f"| 🆘 Escalation sent from {escalation.agent_name} [{ctx.id}] → ref {parent_session_id}"
        )

        try:
            reply: str = await runtime_manager.ask(
                parent_ref, escalation, timeout=_ESCALATION_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            reply = "Meta Agent did not respond in time. Please stop the current subtask gracefully."
            logger.warning(f"| ⏰ Escalation timeout for session {ctx.id}")

        logger.info(f"| 💬 Escalation reply received for session {ctx.id}: {reply}")

        return HookResult(
            decision="allow",
            additional_context=f"[Meta Agent Guidance]\n{reply}",
        )
