"""The preemptive control channel: one coalescing slot per process.

Signals do NOT queue. A process that is told to stop three times stops once, and a
process told to suspend and then to stop is stopped — the stronger signal wins and the
weaker one is dropped rather than applied later, when it would mean something else.

This is the half of process control that must never wait behind ordinary traffic. The
mailbox (:mod:`agentevolver.runtime.mailbox`) is the other half: it is FIFO, it is
delivered only at safe points, and a stop must not sit behind a hundred queued events.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Dict, Optional


class Signal(str, Enum):
    """What the kernel can interrupt a process with."""

    #: Hold at the next step boundary. Messages may still queue.
    SUSPEND = "suspend"
    #: Release a held process.
    RESUME = "resume"
    #: Wind down gracefully at the next step boundary, with a landing hook.
    STOP = "stop"
    #: Abandon at the next action boundary. No landing hook.
    KILL = "kill"


#: Strength order. A raised signal replaces a pending one only if it ranks at least as
#: high, so a KILL is never downgraded to a SUSPEND by a late caller.
_RANK: Dict[Signal, int] = {
    Signal.SUSPEND: 1,
    Signal.RESUME: 1,
    Signal.STOP: 2,
    Signal.KILL: 3,
}


class SignalBox:
    """One process's pending signal, plus an event safe points can wait on.

    Holds at most one signal. ``arrived`` is set while something is pending so a process
    parked in :meth:`Mailbox.wait <agentevolver.runtime.mailbox.Mailbox.wait>` can race
    the two channels and react to a stop without a message ever arriving.
    """

    __slots__ = ("_pending", "_reason", "arrived")

    def __init__(self) -> None:
        self._pending: Optional[Signal] = None
        self._reason: str = ""
        self.arrived: asyncio.Event = asyncio.Event()

    def raise_signal(self, signal: Signal, reason: str = "") -> bool:
        """Post ``signal``, replacing anything weaker already pending.

        Returns:
            True when this call is what the process will see next.
        """
        if self._pending is not None and _RANK[signal] < _RANK[self._pending]:
            return False
        # SUSPEND and RESUME rank equally and cancel each other: the last caller wins,
        # which is what "resume it after all" has to mean.
        self._pending = signal
        self._reason = reason
        self.arrived.set()
        return True

    def take(self) -> Optional[Signal]:
        """Consume the pending signal, or return None. Clears :attr:`arrived`."""
        pending, self._pending = self._pending, None
        self.arrived.clear()
        return pending

    def peek(self) -> Optional[Signal]:
        """Look without consuming."""
        return self._pending

    @property
    def reason(self) -> str:
        """Why the most recent signal was raised; empty when none was given."""
        return self._reason

    @property
    def terminal(self) -> bool:
        """Whether the pending signal ends the process."""
        return self._pending in (Signal.STOP, Signal.KILL)

    def clear(self) -> None:
        """Drop anything pending. Used when a process exits."""
        self._pending = None
        self._reason = ""
        self.arrived.clear()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SignalBox(pending={self._pending.value if self._pending else None!r})"


__all__ = ["Signal", "SignalBox"]
