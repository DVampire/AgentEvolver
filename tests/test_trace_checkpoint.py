"""The log reaches disk before a call that can change the world.

`trace_manager.emit` queues an event and returns; a background writer drains the queue.
That is what keeps recording off the hot path, and it means the log trails the run. The lag
costs nothing right up until the process dies inside it, and then it decides a question
nobody can answer afterwards: a run killed between "about to run this command" and "the
writer caught up" leaves no record of the command, so whether the destructive one executed
is unknowable.

`Agent._checkpoint_before_effects` closes that window, and only for calls that can have
effects — paying a flush on every `read` to protect the `write` would put the writer on the
hot path of the most frequent call in the system.

The three-valued `mutates` flag is the subtle part. `None` means "depends on the arguments",
which is exactly what a shell tool declares, and a shell command is the most likely way an
agent destroys something. Anything but an explicit `False` checkpoints.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agentevolver.agent.types import Agent
from agentevolver.queue import AsyncQueue


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
                await asyncio.sleep(0.01)       # a writer that is not instant
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
    """Deliberately not fail-closed, and the trade is worth stating.

    deepseek-harness refuses to invoke the tool when its checkpoint fails. A wedged writer
    is far rarer than an interrupted run, and an agent that stops working because logging
    is slow is a worse product than one that acts with a gap in its log and says so.
    """
    borrowed_trace._running = True
    borrowed_trace._queue = AsyncQueue(maxsize=4)
    borrowed_trace._queue.emit(1)               # queued, never consumed

    assert asyncio.run(borrowed_trace.flush(timeout=0.05)) is False


# --------------------------------------------------------------------------- #
# When the checkpoint is taken
# --------------------------------------------------------------------------- #
def _checkpointed(mutates, kind: str = "tool", route=("tool", "some_tool")) -> bool:
    """Whether a call with this tool shape flushes the trace before dispatch."""
    agent = Agent.model_construct(name="probe")
    flush = AsyncMock(return_value=True)
    tool = type("StubTool", (), {"mutates": mutates})()

    with patch("agentevolver.tool.server.tool_manager.get", AsyncMock(return_value=tool)):
        with patch("agentevolver.trace.server.trace_manager.flush", flush):
            asyncio.run(agent._checkpoint_before_effects(kind, route))
    return flush.await_count == 1


def test_a_mutating_tool_checkpoints_first():
    """The case this exists for."""
    assert _checkpointed(True)


def test_a_tool_whose_effect_depends_on_its_arguments_also_checkpoints():
    """`None` is what a shell tool declares, and `bash` is how an agent deletes things.

    Reading `None` as "probably safe" is the tempting mistake: it inverts the default for
    the single most dangerous tool in the system, and the failure is silent — the flush
    simply stops happening for the calls that most needed it.
    """
    assert _checkpointed(None)


def test_a_read_only_tool_does_not_pay_for_the_flush():
    """Reads are the most frequent call there is.

    Flushing on every one would move the writer onto the hot path to protect an event whose
    loss costs a gap in a transcript, not an unanswerable question.
    """
    assert not _checkpointed(False)


def test_a_tool_the_registry_cannot_resolve_does_not_block_the_call():
    """A checkpoint that could not be taken is a gap; a checkpoint that raises is an outage.

    The dispatch path must survive a registry lookup that fails, so the guard degrades to
    "no flush" rather than to "no tool call".
    """
    agent = Agent.model_construct(name="probe")
    with patch("agentevolver.tool.server.tool_manager.get", AsyncMock(side_effect=KeyError("gone"))):
        asyncio.run(agent._checkpoint_before_effects("tool", ("tool", "missing")))   # must not raise


@pytest.mark.parametrize("kind,route", [("agent", ("agent", "child")), ("skill", ("skill", "s")), ("tool", None)])
def test_only_tool_calls_checkpoint_here(kind: str, route):
    """A delegated agent runs its own dispatch and checkpoints there.

    Flushing again at the delegation point would double the cost and record nothing new —
    the child's own tool calls are the ones with effects.
    """
    flush = AsyncMock()
    agent = Agent.model_construct(name="probe")
    with patch("agentevolver.trace.server.trace_manager.flush", flush):
        asyncio.run(agent._checkpoint_before_effects(kind, route))

    flush.assert_not_awaited()
