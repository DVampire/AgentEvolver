"""Agent runtime.

An agent is a process and this is its kernel: it owns the states, the two channels and
the lifecycle, and dispatch and subscription are the same mechanism with one flag
between them.

The previous mailbox runtime and the ``protocol`` layer above it are gone. Everything
they offered — delegate, cancel, pause, resume, query, subscribe, publish, escalate,
reply — the kernel offers directly, and having two APIs over one mechanism is how they
drifted apart.
"""

import atexit

from agentevolver.logger import logger

# -- the process kernel ----------------------------------------------------
from agentevolver.runtime.envelopes import (
    Envelope,
    EventEnvelope,
    ReplyEnvelope,
    ReportEnvelope,
    TaskEnvelope,
)
from agentevolver.runtime.errors import (
    InvalidTransition,
    Killed,
    MailboxClosed,
    ProcessDead,
    ProcessNotFound,
    RuntimeKernelError,
    Stopped,
)
from agentevolver.runtime.kernel import Kernel, kernel
from agentevolver.runtime.mailbox import Mailbox
from agentevolver.runtime.process import Process
from agentevolver.runtime.signals import Signal, SignalBox
from agentevolver.runtime.states import ExitStatus, ProcessState
from agentevolver.runtime.topics import TopicRegistry, scoped


def _atexit_warn_on_leak() -> None:
    live = [proc for proc in kernel.list() if proc.alive]
    if live:
        names = ", ".join(f"{proc.name}:{proc.pid[:8]}" for proc in live)
        logger.warning(
            f"| 🪦 kernel: process exiting with {len(live)} live process(es): {names}"
        )


atexit.register(_atexit_warn_on_leak)


__all__ = [
    "Envelope",
    "EventEnvelope",
    "ExitStatus",
    "InvalidTransition",
    "Kernel",
    "Killed",
    "Mailbox",
    "MailboxClosed",
    "Process",
    "ProcessDead",
    "ProcessNotFound",
    "ProcessState",
    "ReplyEnvelope",
    "ReportEnvelope",
    "RuntimeKernelError",
    "Signal",
    "SignalBox",
    "Stopped",
    "TaskEnvelope",
    "TopicRegistry",
    "kernel",
    "scoped",
]
