"""A parent must be able to read the output of the sub-agent it started.

A dispatch hands back a pid, and `job__output` is the action that reads a job's output —
so a parent holding that pid has every reason to call it. But two registries answer to a
job id and only one was ever asked: a backgrounded command is a `job_manager` entry,
while a sub-agent is a kernel process. Asking only `job_manager` failed the read before
any of the action ran, which had a consequence far past the error message: the same
action is where a subscriber's turn is recorded, and that recording is the only writer of
the `collected_turns` a deploy gate requires. A release could not ship no matter what its
subscribers reported, and the error blamed a missing background job.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.agent.types import AgentContext
from agentevolver.runtime.kernel import Kernel
from agentevolver.runtime.modes import InteractionMode


class _Panelist:
    """A subscriber that reports once per event, like a website reviewer."""

    name = "panelist"

    def __init__(self) -> None:
        self.proc = None

    async def __call__(self, task, files=None, ctx=None, **kwargs):
        await self.proc.gate()
        return "report: the hero video plays"

    async def on_event(self, event, payload):
        return None


@pytest_asyncio.fixture
async def subscriber():
    """One subscriber process, woken once, with the job environment loaded."""
    import agentevolver.runtime as runtime
    from agentevolver.environment import environment_manager

    await environment_manager.initialize(["job"])
    kernel = Kernel()
    root = SimpleNamespace(id="s1", extra={"root_session_id": "s1"})
    hub = await kernel.spawn(_Panelist(), "host", mode=InteractionMode.SERVICE, ctx=root)
    child_ctx = AgentContext(name="panelist", extra={"root_session_id": "s1"})
    proc = await kernel.spawn(
        _Panelist(), "you review each release",
        mode=InteractionMode.SUBSCRIBER, ctx=child_ctx, parent=hub, topics=["release"],
    )
    await asyncio.sleep(0.05)
    await kernel.publish_scoped("release", "r1 is live", {}, ctx=root)
    await asyncio.sleep(0.3)

    previous = runtime.kernel._procs
    runtime.kernel._procs = kernel._procs
    try:
        yield proc, environment_manager
    finally:
        runtime.kernel._procs = previous
        await kernel.shutdown(timeout=5)


def _parent_ctx(pid):
    return SimpleNamespace(
        id="s1",
        extra={
            "website_runtime_contract": {"subscriber_job_ids": [pid], "collected_turns": {}},
            "deployment_release_history": [{"release_number": 1}],
        },
    )


@pytest.mark.asyncio
async def test_output_reads_a_subscriber(subscriber):
    """The turn result reaches the parent, rather than a "no such job" failure."""
    proc, environment_manager = subscriber
    result = await environment_manager(
        name="job", action="output",
        input={"job_id": proc.pid, "turn": 1}, ctx=_parent_ctx(proc.pid),
    )
    assert result.success, result.message
    assert "the hero video plays" in str(result.message)


@pytest.mark.asyncio
async def test_reading_records_the_collected_turn(subscriber):
    """The read is what marks the turn collected — the deploy gate reads nothing else."""
    proc, environment_manager = subscriber
    ctx = _parent_ctx(proc.pid)
    await environment_manager(
        name="job", action="output", input={"job_id": proc.pid, "turn": 1}, ctx=ctx,
    )
    contract = ctx.extra["website_runtime_contract"]
    assert contract["collected_turns"] == {proc.pid: 1}
    acceptance = contract["release_acceptance"]["1"][proc.pid]
    assert acceptance["status"] == "accepted"


@pytest.mark.asyncio
async def test_wait_accepts_a_subscriber(subscriber):
    """`wait` resolves the same ids as `output`; one accepting and one refusing a pid
    would let a parent read a sub-agent it is told it cannot wait for."""
    proc, environment_manager = subscriber
    result = await environment_manager(
        name="job", action="wait",
        input={"job_ids": [proc.pid], "condition": "idle_after_turn", "timeout": 10},
        ctx=_parent_ctx(proc.pid),
    )
    assert result.success, result.message


@pytest.mark.asyncio
async def test_an_unknown_id_still_fails(subscriber):
    """Neither registry knows it, so the failure must stay a failure."""
    proc, environment_manager = subscriber
    result = await environment_manager(
        name="job", action="output", input={"job_id": "nope"}, ctx=_parent_ctx(proc.pid),
    )
    assert not result.success


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["exited", "failed", "cancelled", "disappeared"])
async def test_wait_observes_subagent_changes_after_wait_begins(monkeypatch, outcome):
    from agentevolver.runtime import kernel
    from agentevolver.environment.default.job import JobEnvironment

    proc = SimpleNamespace(
        pid="changing-child", state=SimpleNamespace(value="running"), alive=True,
        error="", ended_at=None, started_at=1, session_id="child-session",
        turns=0, busy=True, mailbox=[], resident=False,
    )
    processes = {proc.pid: proc}
    monkeypatch.setattr(kernel, "get", lambda pid: processes.get(pid))
    env = JobEnvironment()
    resolved = asyncio.Event()
    original = env._resolve

    def resolve(pid, ctx):
        result = original(pid, ctx)
        resolved.set()
        return result

    monkeypatch.setattr(env, "_resolve", resolve)
    waiter = asyncio.create_task(env.wait(
        job_ids=[proc.pid], condition="finished", timeout=0.8, ctx=_parent_ctx(proc.pid),
    ))
    try:
        await asyncio.wait_for(resolved.wait(), timeout=1)
        # _as_job returned a snapshot while the process was still running.
        if outcome == "disappeared":
            processes.clear()
        else:
            proc.state.value = outcome
            proc.alive = False
            proc.busy = False
            proc.turns = 1
            proc.error = "failed deliberately" if outcome == "failed" else ""
        result = await asyncio.wait_for(waiter, timeout=2)
        assert not result.get("timed_out", False), result
        if outcome == "disappeared":
            assert not result["success"]
            assert "No job" in result["jobs"][0]["error"]
        else:
            assert result["success"], result
            assert result["jobs"][0]["status"] == outcome
            assert result["jobs"][0]["ready"]
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
