"""One process's inbox: FIFO, bounded only by memory, drained at safe points.

Built on a deque plus an event rather than :class:`asyncio.Queue` for one reason: a
process parked here must be able to wake on a *signal* too, and racing a queue's
``get()`` against another awaitable means either cancelling a coroutine that has
already dequeued an item, or losing it. An explicit ``wait()`` that only reports
non-emptiness lets the caller decide what to take, and take it synchronously.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque, List, Optional

from agentevolver.runtime.envelopes import Envelope
from agentevolver.runtime.errors import MailboxClosed


class Mailbox:
    """FIFO inbox for one process."""

    __slots__ = ("_items", "_arrived", "_closed", "_owner")

    def __init__(self, owner: str = "") -> None:
        self._items: Deque[Envelope] = deque()
        self._arrived: asyncio.Event = asyncio.Event()
        self._closed: bool = False
        self._owner: str = owner

    # -- writing -------------------------------------------------------------

    def put(self, envelope: Envelope) -> None:
        """Append a message and wake anything waiting.

        Raises:
            MailboxClosed: The owning process has exited.
        """
        if self._closed:
            raise MailboxClosed(
                f"process {self._owner or '?'} has exited; it cannot receive "
                f"{envelope.summary()}"
            )
        self._items.append(envelope)
        self._arrived.set()

    # -- reading -------------------------------------------------------------

    def take(self) -> Optional[Envelope]:
        """Pop the oldest message, or None when empty. Never blocks."""
        if not self._items:
            self._arrived.clear()
            return None
        envelope = self._items.popleft()
        if not self._items:
            self._arrived.clear()
        return envelope

    async def wait(self) -> None:
        """Block until at least one message is present.

        Returns immediately when the mailbox is already non-empty. Callers that must
        also react to a signal race this against ``SignalBox.arrived.wait()``.
        """
        if self._items:
            return
        await self._arrived.wait()

    def drain(self) -> List[Envelope]:
        """Take everything queued, oldest first."""
        items = list(self._items)
        self._items.clear()
        self._arrived.clear()
        return items

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> List[Envelope]:
        """Refuse further writes and return whatever was still queued.

        The undelivered remainder is returned rather than dropped silently so the
        kernel can log what a dying process never got to read.
        """
        self._closed = True
        remaining = self.drain()
        # Wake any waiter so it can observe the closure instead of hanging.
        self._arrived.set()
        return remaining

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        # Without this, `if mailbox:` would follow __len__ and read as "has messages",
        # which is true but reads at the call site as "has a mailbox".
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "closed" if self._closed else "open"
        return f"Mailbox(owner={self._owner!r}, queued={len(self._items)}, {state})"


__all__ = ["Mailbox"]
