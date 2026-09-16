"""Generic async queue primitives shared across modules."""

from __future__ import annotations

from typing import Generic, Optional, TypeVar

T = TypeVar("T")

_SENTINEL = object()


class AsyncQueue(Generic[T]):
    """Non-blocking producer, async consumer queue backed by asyncio.Queue.

    Producers call emit() — never awaits, never blocks, drops on overflow.
    Consumers call get() in an async loop — returns None on stop sentinel.

    Usage::

        q: AsyncQueue[MyEvent] = AsyncQueue(maxsize=10_000)

        # producer (sync context ok)
        q.emit(event)

        # consumer
        async def drain():
            while True:
                item = await q.get()
                if item is None:
                    break
                process(item)
                q.task_done()

        # shutdown
        await q.stop()
    """

    import asyncio as _asyncio

    def __init__(self, maxsize: int = 10_000) -> None:
        import asyncio
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def emit(self, item: T) -> bool:
        """Non-blocking put. Returns False (and drops item) if queue is full."""
        try:
            self._queue.put_nowait(item)
            return True
        except Exception:
            return False

    async def get(self) -> Optional[T]:
        """Blocking get. Returns None when stop sentinel is received."""
        item = await self._queue.get()
        if item is _SENTINEL:
            return None
        return item  # type: ignore[return-value]

    def task_done(self) -> None:
        self._queue.task_done()

    async def stop(self) -> None:
        """Inject stop sentinel so consumers exit cleanly."""
        await self._queue.put(_SENTINEL)

    async def join(self) -> None:
        """Wait until every item queued so far has been marked done by the consumer.

        Only meaningful when the consumer calls :meth:`task_done` for each item — the
        documented loop above does. A producer that wants to know its item reached
        durable storage has no other signal: :meth:`emit` returns once the item is
        *queued*, which is exactly the property that makes it non-blocking.
        """
        await self._queue.join()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
