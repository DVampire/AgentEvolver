"""Message-pump loop driving one AgentRef.

The pump owns nothing — it just drains ref._inbox and dispatches messages
to the underlying Agent._run method. On StopMessage it exits cleanly;
on unhandled exception inside the pump itself the ref is marked DEAD.
Per-task exceptions are surfaced through msg.reply_future and do not kill
the pump — the agent can still receive subsequent messages.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.logger import logger
from src.runtime.types import (
    AgentRef,
    AgentStatus,
    StopMessage,
    TaskMessage,
    _current_ref_var,
)

if TYPE_CHECKING:
    from src.agent.types import Agent


async def _pump(agent: "Agent", ref: AgentRef) -> None:
    """Drain ref._inbox and dispatch each message until StopMessage or crash.

    Sets the runtime contextvar so that anything running under this pump's
    asyncio task (including agent._run and any hooks it fires) can look up
    its own ref and parent_ref via runtime_manager.current_ref().
    """
    token = _current_ref_var.set(ref)
    try:
        while True:
            msg = await ref._inbox.get()

            if isinstance(msg, StopMessage):
                logger.info(f"| 🛑 Runtime stop: {ref.name} (reason={msg.reason})")
                return

            if isinstance(msg, TaskMessage):
                try:
                    result = await agent._run(task=msg.task, **msg.kwargs)
                    if msg.reply_future is not None and not msg.reply_future.done():
                        msg.reply_future.set_result(result)
                except asyncio.CancelledError:
                    if msg.reply_future is not None and not msg.reply_future.done():
                        msg.reply_future.cancel()
                    raise
                except Exception as exc:
                    logger.error(f"| ❌ Runtime task failed: {ref.name}: {exc}", exc_info=True)
                    if msg.reply_future is not None and not msg.reply_future.done():
                        msg.reply_future.set_exception(exc)
                continue

            logger.warning(f"| ⚠️ Runtime got unknown message: {type(msg).__name__} on {ref.name}")

    except asyncio.CancelledError:
        logger.info(f"| ✋ Runtime pump cancelled: {ref.name}")
        raise
    except Exception as exc:
        logger.error(f"| 💀 Runtime pump crashed: {ref.name}: {exc}", exc_info=True)
        ref.status = AgentStatus.DEAD
    finally:
        _current_ref_var.reset(token)
