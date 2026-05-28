"""EscalationHook — routes ON_ESCALATE to the parent AgentRef via the runtime registry.

Design
------
- parent_session_id (= MetaAgent's ref.name) is passed in the hook input dict by
  sub-agents that support escalation (e.g. ReasonActAgent includes it from ctx.parent_session_id).
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

        escalation = EscalationMessage(
            task_id    = inp.get("task_id") or ctx.id,
            agent_name = inp.get("agent_name", ""),
            session_id = ctx.id,
            reason     = inp.get("reason") or "",
            situation  = inp.get("situation") or "",
            suggestion = inp.get("suggestion") or "",
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
