"""Agent runtime: mailbox + pump + lifecycle on top of registered Agent instances."""

import atexit

from agentevolver.logger import logger
from agentevolver.runtime.server import RuntimeManager, runtime_manager
from agentevolver.runtime.types import (
    AgentDeadError,
    AgentRef,
    AgentStatus,
    BaseMessage,
    ControlMessage,
    QueryMessage,
    StopMessage,
    SubscriptionEventMessage,
    TaskMessage,
)


def _atexit_warn_on_leak() -> None:
    leaks = runtime_manager.list()
    if leaks:
        names = ", ".join(r.name for r in leaks)
        logger.warning(
            f"| 🪦 runtime: process exiting with {len(leaks)} non-stopped ref(s): {names}"
        )


atexit.register(_atexit_warn_on_leak)


__all__ = [
    "runtime_manager",
    "RuntimeManager",
    "AgentRef",
    "AgentStatus",
    "AgentDeadError",
    "BaseMessage",
    "ControlMessage",
    "QueryMessage",
    "TaskMessage",
    "StopMessage",
    "SubscriptionEventMessage",
]
