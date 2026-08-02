"""AsyncQueue: a non-blocking producer feeding an async consumer.

The contract that matters to callers is that ``emit`` never blocks and never
raises — a full queue drops the item and says so — and that ``stop`` unblocks a
waiting consumer instead of leaving it hanging.
"""

import asyncio

import pytest

from agentevolver.queue import AsyncQueue


@pytest.mark.asyncio
async def test_items_come_back_in_order():
    queue: AsyncQueue[int] = AsyncQueue()
    for n in range(3):
        assert queue.emit(n) is True
    assert [await queue.get() for _ in range(3)] == [0, 1, 2]


@pytest.mark.asyncio
async def test_a_full_queue_drops_instead_of_blocking():
    queue: AsyncQueue[int] = AsyncQueue(maxsize=2)
    assert queue.emit(1) is True
    assert queue.emit(2) is True
    # Third emit has nowhere to go. It must report the drop, not block or raise.
    assert queue.emit(3) is False
    assert queue.qsize() == 2


@pytest.mark.asyncio
async def test_stop_releases_a_waiting_consumer_with_none():
    queue: AsyncQueue[int] = AsyncQueue()

    async def drain():
        seen = []
        while True:
            item = await queue.get()
            if item is None:
                return seen
            seen.append(item)
            queue.task_done()

    consumer = asyncio.create_task(drain())
    queue.emit("a")
    queue.emit("b")
    await queue.stop()
    assert await asyncio.wait_for(consumer, timeout=2) == ["a", "b"]


@pytest.mark.asyncio
async def test_a_consumer_started_before_any_producer_still_receives():
    """The consumer normally wins the race to the queue; it must simply wait."""
    queue: AsyncQueue[str] = AsyncQueue()
    pending = asyncio.create_task(queue.get())
    await asyncio.sleep(0)  # let the consumer reach the await
    queue.emit("late")
    assert await asyncio.wait_for(pending, timeout=2) == "late"


@pytest.mark.asyncio
async def test_emptiness_tracks_what_is_pending():
    queue: AsyncQueue[int] = AsyncQueue()
    assert queue.empty() is True
    queue.emit(1)
    assert queue.empty() is False
    await queue.get()
    assert queue.empty() is True


@pytest.mark.asyncio
async def test_stop_is_queued_behind_pending_items_not_ahead_of_them():
    """A stop must not swallow work already emitted."""
    queue: AsyncQueue[int] = AsyncQueue()
    queue.emit(1)
    await queue.stop()
    assert await queue.get() == 1
    assert await queue.get() is None
