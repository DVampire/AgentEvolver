"""EscalationHook — bridges sub-agent ON_ESCALATE → MetaAgent via runtime.ask.

Design
------
- Registered once at startup (stateless instance).
- At fire time the sub-agent's own AgentRef and its parent_ref are looked up
  through the runtime contextvar (``runtime_manager.current_ref()``).
- An ``EscalationMessage`` is then sent via ``runtime_manager.ask`` to the
  parent — the runtime auto-fills its reply_future and awaits it. The hook
  returns the parent's guidance as ``additional_context`` so the sub-agent's
  next turn sees a system reminder.

No per-session scratch state is involved; the only coupling between sub-agent
and MetaAgent is the parent_ref captured by runtime.spawn.
"""

from __future__ import annotations

import asyncio

from src.hook.types import Hook, HookContext, HookEvent, HookResult
from src.logger import logger
from src.registry import HOOK
from src.runtime import current_ref, runtime_manager

_ESCALATION_TIMEOUT_S = 300.0


@HOOK.register_module(force=True)
class EscalationHook(Hook):
    """Global singleton hook that suspends a sub-agent on ON_ESCALATE
    and asks MetaAgent for guidance through the runtime."""

    name: str = "escalation_hook"
    description: str = "Suspends a sub-agent on ON_ESCALATE and awaits MetaAgent reply."
    events: list = [HookEvent.ON_ESCALATE]
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        sub_ref = current_ref()
        if sub_ref is None:
            logger.warning("| ⚠️ EscalationHook: no current ref (not running under runtime?)")
            return HookResult.allow()

        parent_ref = sub_ref.parent_ref
        if parent_ref is None:
            logger.warning(
                f"| ⚠️ EscalationHook: {sub_ref.name} has no parent_ref — nowhere to escalate"
            )
            return HookResult.allow()

        from src.agent.actor.meta_agent import EscalationMessage  # local: break import cycle

        extra = ctx.extra or {}
        escalation = EscalationMessage(
            task_id=sub_ref.name,
            agent_name=sub_ref.agent_name,
            session_id=ctx.id,
            reason=extra.get("reason", ""),
            situation=extra.get("situation", ""),
            suggestion=extra.get("suggestion", ""),
        )

        logger.info(
            f"| 🆘 Escalation sent from {sub_ref.agent_name} [{sub_ref.name}] → {parent_ref.name}"
        )

        try:
            reply: str = await runtime_manager.ask(
                parent_ref, escalation, timeout=_ESCALATION_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            reply = "Meta Agent did not respond in time. Please stop the current subtask gracefully."
            logger.warning(f"| ⏰ Escalation timeout for task {sub_ref.name}")

        logger.info(f"| 💬 Escalation reply received for task {sub_ref.name}: {reply}")

        return HookResult(
            decision="allow",
            additional_context=f"[Meta Agent Guidance]\n{reply}",
        )
