"""AsyncQueue: a non-blocking producer feeding an async consumer.

Its one caller in this repo is tracing — `trace/server.py` emits `TraceEvent`s from
whatever context an agent happens to be running in, and `trace/writer.py` drains them to
JSONL. That shape sets the contract: `emit` never blocks and never raises, so a full or
broken queue costs a dropped event rather than a stalled or crashed run, and `stop` has to
release a consumer that is parked on `get` rather than leaving the writer task hanging at
shutdown. The failures this file prevents are the quiet ones — a run that hangs inside its
own observability, or a shutdown that never completes because nothing woke the drain loop.
"""

import asyncio

import pytest

from agentevolver.queue import AsyncQueue


# --------------------------------------------------------------------------- #
# What a producer gets back
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_items_come_back_in_the_order_they_were_emitted():
    queue: AsyncQueue[int] = AsyncQueue()
    for n in range(3):
        assert queue.emit(n) is True
    assert [await queue.get() for _ in range(3)] == [0, 1, 2]


@pytest.mark.asyncio
async def test_a_full_queue_drops_instead_of_blocking():
    """Overflow is reported as a return value, not raised and not waited out.

    The producer is often a synchronous trace call inside an agent step. Blocking there
    would make a slow writer look like a slow agent, and raising would let an
    observability queue abort work it is only meant to observe. `False` is the only
    outcome that keeps the drop visible without either.
    """
    queue: AsyncQueue[int] = AsyncQueue(maxsize=2)
    assert queue.emit(1) is True
    assert queue.emit(2) is True
    # Third emit has nowhere to go. It must report the drop, not block or raise.
    assert queue.emit(3) is False
    assert queue.qsize() == 2


@pytest.mark.asyncio
async def test_emptiness_tracks_what_is_pending():
    queue: AsyncQueue[int] = AsyncQueue()
    assert queue.empty() is True
    queue.emit(1)
    assert queue.empty() is False
    await queue.get()
    assert queue.empty() is True


# --------------------------------------------------------------------------- #
# Waking and shutting down the consumer
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_stop_releases_a_waiting_consumer_with_none():
    """`None` is the drain loop's only exit signal, so it has to arrive at an idle wait.

    At shutdown the writer task is almost always parked in `await get()` with nothing
    left to read. If the sentinel only reached a consumer that happened to be awake, the
    task would sit there forever and the process would not come down.
    """
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
    """The consumer normally wins the race to the queue; it must simply wait.

    Startup order is not controlled: the writer task is created before the first agent
    step runs, so the common case is an empty queue with someone already waiting on it.
    """
    queue: AsyncQueue[str] = AsyncQueue()
    pending = asyncio.create_task(queue.get())
    await asyncio.sleep(0)  # let the consumer reach the await
    queue.emit("late")
    assert await asyncio.wait_for(pending, timeout=2) == "late"


@pytest.mark.asyncio
async def test_stop_is_queued_behind_pending_items_not_ahead_of_them():
    """A stop must not swallow work already emitted.

    The sentinel goes through the same queue as the data, so everything emitted before
    the stop is still read first. A stop implemented as a flag or a cancel would instead
    discard the tail of the trace — and the events lost that way are exactly the ones
    written while the run was shutting down, which is when they matter most.
    """
    queue: AsyncQueue[int] = AsyncQueue()
    queue.emit(1)
    await queue.stop()
    assert await queue.get() == 1
    assert await queue.get() is None
