"""EscalationHook — global singleton hook that handles ON_ESCALATE events.

Design
------
The hook is registered once at startup (stateless instance).  Per-subtask
mutable state (meta inbox, reply future) is stored in
``HookSessionState.scratch["escalation"]`` keyed by the subtask's session_id.

MetaAgent writes into scratch before dispatching a subtask::

    session_state = await hook_manager.context.get_or_create(subtask_session_id)
    session_state.scratch["escalation"] = {
        "meta_inbox": state.inbox,
        "reply_future": reply_future,
        "task_id": task_id,
        "agent_name": agent_name,
    }

When the sub-agent fires HookEvent.ON_ESCALATE, handle() reads that entry
from ctx.extra["_session_state"] (injected by HookManager), sends a
SUBTASK_ESCALATE event to meta_inbox, then awaits reply_future.  The hook
suspends until MetaAgent resolves the future, then returns a HookResult
whose additional_context is injected as a system reminder into the sub-agent.

Concurrency
-----------
Each subtask session has its own HookSessionState, so concurrent subtasks
never share state.  No per-subtask hook registration/unregistration needed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from src.hook.types import Hook, HookContext, HookEvent, HookResult
from src.logger import logger

_ESCALATION_TIMEOUT_S = 300.0


class EscalationHook(Hook):
    """Global singleton hook that bridges sub-agent → MetaAgent escalation."""

    name: str = "escalation_hook"
    description: str = "Suspends a sub-agent on ON_ESCALATE and awaits MetaAgent reply."
    events: list = [HookEvent.ON_ESCALATE]
    priority: int = 10

    async def handle(self, ctx: HookContext) -> HookResult:
        session_state = (ctx.extra or {}).get("_session_state")
        if session_state is None:
            logger.warning("| ⚠️ EscalationHook: no session state in context")
            return HookResult.allow()

        cfg: Optional[Dict[str, Any]] = session_state.scratch.get("escalation")
        if cfg is None:
            logger.warning(f"| ⚠️ EscalationHook: no escalation config for session {ctx.id}")
            return HookResult.allow()

        meta_inbox: asyncio.Queue = cfg["meta_inbox"]
        task_id: str = cfg["task_id"]
        agent_name: str = cfg["agent_name"]

        # Each escalation gets a fresh future so multiple escalations from the
        # same subtask are handled independently.
        reply_future: asyncio.Future = asyncio.get_event_loop().create_future()
        cfg["reply_future"] = reply_future

        # Import here to avoid circular deps at module load time.
        from src.agent.default.meta_agent import MetaEvent, MetaEventType

        extra = ctx.extra or {}
        reason = extra.get("reason", "")
        situation = extra.get("situation", "")
        suggestion = extra.get("suggestion", "")

        escalation_text = f"Reason: {reason}\nSituation: {situation}"
        if suggestion:
            escalation_text += f"\nSuggestion: {suggestion}"

        event = MetaEvent(
            event_type=MetaEventType.SUBTASK_ESCALATE,
            task_id=task_id,
            agent_name=agent_name,
            session_id=ctx.id,
            escalation_message=escalation_text,
            reply_future=reply_future,
        )

        await meta_inbox.put(event)
        logger.info(f"| 🆘 Escalation sent from {agent_name} [{task_id[:8]}]")

        try:
            reply: str = await asyncio.wait_for(
                asyncio.shield(reply_future), timeout=_ESCALATION_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            reply = "Meta Agent did not respond in time. Please stop the current subtask gracefully."
            logger.warning(f"| ⏰ Escalation timeout for task {task_id}")

        logger.info(f"| 💬 Escalation reply received for task {task_id[:8]}: {reply[:80]}")

        return HookResult(
            decision="allow",
            additional_context=f"[Meta Agent Guidance]\n{reply}",
        )
