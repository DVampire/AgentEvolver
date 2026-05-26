"""Agent runtime: mailbox + pump + lifecycle on top of registered Agent instances."""

import atexit

from src.logger import logger
from src.runtime.server import RuntimeManager, runtime_manager
from src.runtime.types import (
    AgentDeadError,
    AgentRef,
    AgentStatus,
    BaseMessage,
    StopMessage,
    TaskMessage,
)


def current_ref() -> "AgentRef | None":
    """Module-level shortcut for ``runtime_manager.current_ref()``.

    Returns the AgentRef whose pump is driving the calling asyncio task,
    or None if called outside any pump.
    """
    return runtime_manager.current_ref()


def _atexit_warn_on_leak() -> None:
    """At interpreter exit, surface any refs that never got stopped.

    We can't run async cleanup from atexit (the event loop is usually gone),
    so this is best-effort visibility — well-behaved apps should still call
    ``await runtime_manager.shutdown()`` in their main's finally.
    """
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
    "current_ref",
    "AgentRef",
    "AgentStatus",
    "AgentDeadError",
    "BaseMessage",
    "TaskMessage",
    "StopMessage",
]
