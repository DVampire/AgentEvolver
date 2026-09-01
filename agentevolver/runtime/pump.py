"""Message-pump loop driving one AgentRef.

The pump owns nothing — it drains ref._inbox and dispatches every message
to agent.handle(msg, ref).  On StopMessage it exits cleanly; on an
unhandled pump-level exception the ref is marked DEAD.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentevolver.logger import logger
from agentevolver.runtime.types import AgentRef, AgentStatus, StopMessage

if TYPE_CHECKING:
    from agentevolver.agent.types import Agent


async def _pump(agent: "Agent", ref: AgentRef) -> None:
    """Drain ref._inbox and call agent.handle(msg, ref) until StopMessage or crash."""
    try:
        while True:
            msg = await ref._inbox.get()

            if isinstance(msg, StopMessage):
                logger.info(f"| 🛑 Runtime stop: {ref.name} (reason={msg.reason})")
                return

            await agent.handle(msg, ref)

    except asyncio.CancelledError:
        logger.info(f"| ✋ Runtime pump cancelled: {ref.name}")
        raise
    except Exception as exc:
        logger.error(f"| 💀 Runtime pump crashed: {ref.name}: {exc}", exc_info=True)
        ref.status = AgentStatus.DEAD
        # A pump is the only consumer of this ref. Wake the active ask immediately;
        # otherwise the root invocation or delegated driver waits forever on a future
        # that no coroutine can now resolve.
        pending = ref._pending_reply
        ref._pending_reply = None
        if pending is not None and not pending.done():
            pending.set_exception(exc)
        for queue in (ref._inbox, ref._tasks):
            while not queue.empty():
                try:
                    queued = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                future = getattr(queued, "reply_future", None)
                if future is not None and not future.done():
                    future.set_exception(exc)
