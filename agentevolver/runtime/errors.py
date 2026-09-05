"""Kernel errors, and the two control-flow exceptions a safe point raises.

``Stopped`` and ``Killed`` derive from ``BaseException``, not ``Exception``. Agent code
is full of defensive ``except Exception`` blocks around tool calls and model calls; if a
stop were an ordinary exception, one of those would swallow it and the process would
carry on after the user asked it to end. This is the same reason ``CancelledError`` is a
``BaseException``, and it is why a stop cannot be "handled" by accident.
"""

from __future__ import annotations

from typing import Optional


class RuntimeKernelError(Exception):
    """Base class for errors the kernel raises at its own API boundary."""


class BudgetExhausted(RuntimeKernelError):
    """A process or its shared run has exhausted its declared resource budget."""


class InvalidTransition(RuntimeKernelError):
    """A state move that is not in the transition table was requested."""


class ProcessNotFound(RuntimeKernelError):
    """No live process answers to this pid."""


class ProcessDead(RuntimeKernelError):
    """The target process has exited and cannot accept the request."""


class MailboxClosed(RuntimeKernelError):
    """A message was posted to a process that has finished."""


class ControlFlowSignal(BaseException):
    """A signal delivered at a safe point, unwinding the agent's own call stack.

    Deliberately outside the ``Exception`` hierarchy — see the module docstring.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


class Stopped(ControlFlowSignal):
    """Graceful stop delivered at a step boundary.

    The conversation is whole at this point, so the kernel gives the agent its
    ``on_land`` hook and the run's partial result is usable.
    """


class Killed(ControlFlowSignal):
    """Forced stop delivered at an action boundary.

    Actions that had not started are abandoned. The conversation may be incomplete, so
    the process exits ``CANCELLED`` without a landing hook.
    """


def describe(error: Optional[BaseException]) -> str:
    """One short line naming an exception, for logs and exit records."""
    if error is None:
        return ""
    return f"{type(error).__name__}: {error}"


__all__ = [
    "BudgetExhausted",
    "ControlFlowSignal",
    "InvalidTransition",
    "Killed",
    "MailboxClosed",
    "ProcessDead",
    "ProcessNotFound",
    "RuntimeKernelError",
    "Stopped",
    "describe",
]
