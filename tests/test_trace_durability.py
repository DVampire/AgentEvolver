"""The log reaches disk before a call that can change the world.

`trace_manager.emit` queues an event and returns; a background writer drains the queue.
That is what keeps recording off the hot path, and it means the log trails the run. The lag
costs nothing right up until the process dies inside it, and then it decides a question
nobody can answer afterwards: a run killed between "about to run this command" and "the
writer caught up" leaves no record of the command, so whether the destructive one executed
is unknowable.

The authoritative Tool pipeline closes that window after policy and approval settle and
immediately before the body. It does so only for calls that can have effects — paying a
flush on every `read` to protect the `write` would put the writer on the hot path of the
most frequent call in the system.

The three-valued `mutates` flag is the subtle part. `None` means "depends on the arguments",
which is exactly what a shell tool declares, and a shell command is the most likely way an
agent destroys something. Anything but an explicit `False` crosses the durability boundary.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentevolver.response import Response, ResponseType
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.types import Tool, ToolContext
from agentevolver.utils import AsyncQueue


# --------------------------------------------------------------------------- #
# The flush primitive
# --------------------------------------------------------------------------- #
def test_a_queue_join_waits_for_the_consumer_to_finish():
    """`emit` returning means "queued", never "written".

    Without a join there is no signal a producer can wait on, which is why the trace had
    no way to answer "is my event on disk yet" before this existed.
    """

    async def run():
        queue: AsyncQueue[int] = AsyncQueue(maxsize=8)
        written: list[int] = []

        async def consume():
            while True:
                item = await queue.get()
                if item is None:
                    break
                await asyncio.sleep(0.01)  # a writer that is not instant
                written.append(item)
                queue.task_done()

        worker = asyncio.create_task(consume())
        for value in range(5):
            queue.emit(value)
        assert written != list(range(5)), "the test needs the writer to still be behind"

        await queue.join()
        assert written == list(range(5))

        await queue.stop()
        await worker

    asyncio.run(run())


@pytest.fixture
def borrowed_trace():
    """The real `trace_manager`, with its queue state put back afterwards.

    `TraceManager` is a `Singleton`: constructing one in a test returns the process-wide
    instance every other test shares, so driving it means borrowing it rather than making
    one. Restoring both fields is what keeps the next test from inheriting a queue nobody
    is draining.
    """
    from agentevolver.trace.server import trace_manager

    saved = (trace_manager._running, trace_manager._queue)
    yield trace_manager
    trace_manager._running, trace_manager._queue = saved


def test_a_flush_with_no_writer_running_returns_immediately(borrowed_trace):
    """Model calls and tests happen outside a live trace.

    Waiting on a queue nobody is draining would hang forever, so "not running" has to mean
    "nothing to wait for" rather than "wait".
    """
    borrowed_trace._running = False

    assert asyncio.run(borrowed_trace.flush()) is True


def test_a_flush_that_does_not_drain_gives_up_rather_than_hanging(borrowed_trace):
    """The primitive reports timeout; the selected integrity profile settles policy."""
    borrowed_trace._running = True
    borrowed_trace._queue = AsyncQueue(maxsize=4)
    borrowed_trace._queue.emit(1)  # queued, never consumed

    assert asyncio.run(borrowed_trace.flush(timeout=0.05)) is False


# --------------------------------------------------------------------------- #
# When durability is required
# --------------------------------------------------------------------------- #
def _made_durable(mutates, kind: str = "tool", route=("tool", "some_tool")) -> bool:
    """Whether this registered Tool flushes after guards and before its body."""

    class StubTool(Tool):
        name: str = "some_tool"
        description: str = "Test durability-boundary placement."

        async def __call__(self, **kwargs):
            return Response(type=ResponseType.TOOL, success=True, message="ran")

    manager = ToolContextManager()
    tool = StubTool(mutates=mutates)

    async def get_info(name):
        return SimpleNamespace(version="1.0.0", instance=tool)

    manager.get_info = get_info
    flush = AsyncMock(return_value=True)
    with patch("agentevolver.trace.server.trace_manager.flush", flush):
        asyncio.run(
            manager(
                name="some_tool",
                input={},
                ctx=ToolContext(id="session-1"),
            )
        )
    return flush.await_count == 1


def test_a_mutating_tool_makes_trace_durable_first():
    """The case this exists for."""
    assert _made_durable(True)


def test_argument_dependent_effect_also_makes_trace_durable():
    """`None` is what a shell tool declares, and `bash` is how an agent deletes things.

    Reading `None` as "probably safe" is the tempting mistake: it inverts the default for
    the single most dangerous tool in the system, and the failure is silent — the flush
    simply stops happening for the calls that most needed it.
    """
    assert _made_durable(None)


def test_a_read_only_tool_does_not_pay_for_the_flush():
    """Reads are the most frequent call there is.

    Flushing on every one would move the writer onto the hot path to protect an event whose
    loss costs a gap in a transcript, not an unanswerable question.
    """
    assert not _made_durable(False)


def test_an_unresolved_tool_does_not_flush_or_enter_a_body():
    manager = ToolContextManager()
    manager.get_info = AsyncMock(return_value=None)
    flush = AsyncMock(return_value=True)
    with patch("agentevolver.trace.server.trace_manager.flush", flush):
        response = asyncio.run(
            manager(
                name="missing",
                input={},
                ctx=ToolContext(id="session-1"),
            )
        )
    flush.assert_not_awaited()
    assert response.extra["execution"]["error_code"] == "not_found"


def test_a_preflight_denial_does_not_pay_for_a_durability_flush():
    class StubTool(Tool):
        name: str = "some_tool"
        description: str = "Must not run."
        mutates: bool = True

        async def __call__(self, **kwargs):  # pragma: no cover - denial owns the claim
            raise AssertionError("denied body ran")

    manager = ToolContextManager()
    manager.get_info = AsyncMock(return_value=SimpleNamespace(version="1.0.0", instance=StubTool()))
    flush = AsyncMock(return_value=True)
    with patch("agentevolver.trace.server.trace_manager.flush", flush):
        response = asyncio.run(
            manager(
                name="some_tool",
                input={},
                ctx=ToolContext(id="session-1"),
                execution_context={"guard_denials": ["plan mode is active"]},
            )
        )
    flush.assert_not_awaited()
    assert response.extra["execution"]["error_code"] == "policy_denied"
