"""What the mailbox runtime promises about a ref: it is registered while alive, and gone after.

Every running agent is one `AgentRef` in a single registry keyed by name, and the protocol
layer resolves a parent from `parent_session_id` by looking that name up. So the registry
is not bookkeeping — it is the address book. A ref left behind after a stop keeps its name
taken and hands later lookups a handle whose inbox nothing drains; a ref removed while its
pump is still alive orphans the pump.

The other half is failure containment. One agent's pump is long-lived and serves many
asks, so a task that raises, or a caller that gives up waiting, must cost exactly that one
task. The `ask` timeout path shields the reply future for this reason: cancelling a future
the agent still owns turns its normal completion into an `InvalidStateError` inside the
pump, which kills the agent for every later caller rather than the one that timed out.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentevolver.runtime import AgentDeadError, AgentStatus, TaskMessage, runtime_manager


class StubAgent:
    """Small protocol-compatible agent without importing the LLM stack."""

    name = "stub"

    def __init__(self, *, delay: float = 0, fail_on: str | None = None) -> None:
        self.delay = delay
        self.fail_on = fail_on

    async def handle(self, msg: Any, ref: Any) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if msg.task == self.fail_on:
            msg.reply_future.set_exception(ValueError("boom"))
            return
        msg.reply_future.set_result({"task": msg.task, "kwargs": msg.kwargs, "ref": ref.name})


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Registration and deregistration
# --------------------------------------------------------------------------- #
def test_a_one_shot_invoke_forwards_its_kwargs_and_leaves_no_ref_behind() -> None:
    """`invoke` is spawn + ask + stop, and the stop is the part worth pinning.

    Callers use it for short delegated runs, often many under one name pattern. Every
    kwarg except `task` has to arrive as part of the message rather than being consumed
    by the runtime — `ctx` and `parent_ref` in particular are how the callee finds its
    caller. And because the ref is registered by name for the duration, a missing cleanup
    would make the second invoke under the same name collide with the first.
    """

    async def check() -> None:
        result = await runtime_manager.invoke(
            StubAgent(), name="one-shot", task="hello", parent_ref="parent", ctx={"id": "ctx"}
        )
        assert result == {
            "task": "hello",
            "kwargs": {"parent_ref": "parent", "ctx": {"id": "ctx"}},
            "ref": "one-shot",
        }
        assert runtime_manager.get("one-shot") is None

    run(check())


def test_a_stopped_ref_is_deregistered_and_refuses_further_messages() -> None:
    """Stopping has to be visible from all three sides: status, registry, and send.

    A stopped agent whose name still resolves is the dangerous state — the protocol layer
    looks refs up by name, so a caller would get a handle, `send` into an inbox no pump is
    reading, and wait forever with nothing to show for it. Raising `AgentDeadError`
    converts that silent hang into an immediate error at the call site.
    """

    async def check() -> None:
        ref = await runtime_manager.spawn(StubAgent(), name="lifecycle")
        assert ref.status is AgentStatus.RUNNING
        result = await runtime_manager.ask(ref, TaskMessage(task="ping"))
        assert result["task"] == "ping"
        await runtime_manager.stop(ref)
        assert ref.status is AgentStatus.STOPPED
        assert runtime_manager.get(ref.name) is None
        with pytest.raises(AgentDeadError):
            await runtime_manager.send(ref, TaskMessage(task="ghost"))

    run(check())


def test_two_running_agents_cannot_share_one_ref_name() -> None:
    """Names are addresses, so a silent overwrite would misroute every later message.

    Spawning over a live name is a caller bug — reusing a session id, restarting an agent
    without stopping it. If the registry simply replaced the entry, the first agent's pump
    would keep running with nothing able to reach it, and messages meant for it would land
    on the newcomer. Refusing at spawn puts the error where the mistake is.
    """

    async def check() -> None:
        ref = await runtime_manager.spawn(StubAgent(), name="duplicate")
        try:
            with pytest.raises(ValueError, match="collision"):
                await runtime_manager.spawn(StubAgent(), name="duplicate")
        finally:
            await runtime_manager.stop(ref)

    run(check())


# --------------------------------------------------------------------------- #
# One failure stays one failure
# --------------------------------------------------------------------------- #
def test_a_callers_timeout_does_not_kill_the_agent_it_was_waiting_on() -> None:
    """The waiter gives up; the agent does not.

    `ask(timeout=...)` awaits a future the agent still owns and will complete. Timing out
    by cancelling that future is the obvious implementation and the wrong one: the
    in-flight handler then calls `set_result` on a cancelled future, raises
    `InvalidStateError` inside the pump, and takes the whole agent down — so one slow call
    would end a long-lived agent that many other callers still hold a ref to. Shielding
    the future keeps the damage inside the caller.
    """

    async def check() -> None:
        # The handler sleeps 0.2s and the caller waits 0.01s, so the timeout is certain;
        # the 0.25s sleep afterwards lets that first handler finish and try to reply,
        # which is the moment an unshielded future would poison the pump.
        ref = await runtime_manager.spawn(StubAgent(delay=0.2), name="slow")
        with pytest.raises(asyncio.TimeoutError):
            await runtime_manager.ask(ref, TaskMessage(task="wait"), timeout=0.01)
        await asyncio.sleep(0.25)
        assert ref.status is AgentStatus.RUNNING
        assert (await runtime_manager.ask(ref, TaskMessage(task="next")))["task"] == "next"
        await runtime_manager.stop(ref)
        assert runtime_manager.get(ref.name) is None

    run(check())


def test_a_task_that_raises_is_the_tasks_failure_not_the_agents() -> None:
    """A rejected reply future must reach the caller without disturbing the pump.

    The pump marks a ref DEAD on an unhandled exception, which is right for a pump-level
    crash and wrong for an error the handler deliberately reported. The distinction is
    easy to lose — both are `ValueError` on the way out — and losing it means one bad task
    ends the agent, so the next caller sees `AgentDeadError` for a fault that had nothing
    to do with it.
    """

    async def check() -> None:
        ref = await runtime_manager.spawn(StubAgent(fail_on="boom"), name="resilient")
        try:
            with pytest.raises(ValueError, match="boom"):
                await runtime_manager.ask(ref, TaskMessage(task="boom"))
            assert ref.status is AgentStatus.RUNNING
            assert (await runtime_manager.ask(ref, TaskMessage(task="ok")))["task"] == "ok"
        finally:
            await runtime_manager.stop(ref)

    run(check())


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #
def test_shutdown_empties_the_registry_and_a_ref_still_says_who_it_is() -> None:
    """Shutdown is undrained and must not skip refs it failed to stop cleanly.

    The runtime manager is a process-wide singleton, so anything it still holds after
    shutdown survives into the next run and keeps its name reserved. The `repr` assertions
    ride along because that string is what the runtime logs on every spawn and stop: a ref
    that prints without its name or status makes those logs useless for exactly the
    lifecycle bugs this file is about.
    """

    async def check() -> None:
        first = await runtime_manager.spawn(StubAgent(), name="shutdown-a")
        await runtime_manager.spawn(StubAgent(), name="shutdown-b")
        assert "name='shutdown-a'" in repr(first)
        assert "running" in repr(first)
        await runtime_manager.shutdown()
        assert runtime_manager.list() == []

    run(check())
