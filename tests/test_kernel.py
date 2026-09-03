"""The process kernel: states, the two channels, and the two modes.

An agent is a process and the kernel owns its life. What is worth pinning here is not
that a turn runs — that is the loop's test — but the properties the previous runtime got
wrong: that IDLE is a state distinct from EXITED, that a stop is honoured only at a safe
point, that every ending goes through one exit path, and that dispatch and subscription
are the same mechanism.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.runtime.envelopes import ReportEnvelope, TaskEnvelope
from agentevolver.runtime.errors import InvalidTransition
from agentevolver.runtime.kernel import Kernel
from agentevolver.runtime.states import ExitStatus, ProcessState, check_transition


class Steps:
    """A process with no model: N awaited steps, each through the safe point."""

    def __init__(self, name="steps", steps=3, sleep=0.01):
        self.name = name
        self.steps = steps
        self.sleep = sleep
        self.log: list[str] = []
        self.events: list = []

    async def __call__(self, task, files=None, ctx=None, **kwargs):
        for index in range(self.steps):
            await self.proc.gate()
            await asyncio.sleep(self.sleep)
            self.log.append(f"step{index}")
        return f"{self.name}:{task}"

    async def on_event(self, envelope, proc):
        self.events.append(envelope)

    async def on_start(self, task, proc):
        self.log.append("on_start")

    async def on_land(self, reason):
        self.log.append(f"on_land:{reason}")

    async def on_exit(self, status):
        self.log.append(f"on_exit:{status.value}")

    async def on_suspend(self):
        self.log.append("on_suspend")

    async def on_resume(self):
        self.log.append("on_resume")


@pytest_asyncio.fixture
async def kernel():
    instance = Kernel()
    try:
        yield instance
    finally:
        await instance.shutdown(timeout=5)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def test_the_transition_table_refuses_a_move_it_does_not_have():
    """A bad move is an error, not a state silently coerced into something legal."""
    check_transition(ProcessState.RUNNING, ProcessState.IDLE)
    with pytest.raises(InvalidTransition):
        check_transition(ProcessState.EXITED, ProcessState.RUNNING)
    with pytest.raises(InvalidTransition):
        check_transition(ProcessState.NEW, ProcessState.EXITED)


@pytest.mark.asyncio
async def test_a_one_shot_process_runs_once_and_exits_done(kernel):
    agent = Steps(steps=2)
    proc = await kernel.spawn(agent, "hello")
    assert await kernel.wait(proc, timeout=5) == "steps:hello"
    assert proc.exit_status is ExitStatus.DONE
    assert proc.turns == 1
    assert proc.state is ProcessState.EXITED


@pytest.mark.asyncio
async def test_an_agent_fault_ends_the_process_and_not_the_kernel(kernel):
    class Boom(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            raise ValueError("nope")

    proc = await kernel.spawn(Boom(), "x")
    await kernel.wait(proc, timeout=5)
    assert proc.exit_status is ExitStatus.FAILED
    assert "nope" in proc.error
    # The table still answers, which is the point of catching it here.
    assert kernel.list(alive_only=False)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_holds_at_a_step_boundary_and_resume_continues(kernel):
    agent = Steps(steps=8, sleep=0.05)
    proc = await kernel.spawn(agent, "long")
    await asyncio.sleep(0.06)
    assert await kernel.suspend(proc)
    await asyncio.sleep(0.12)

    assert proc.state is ProcessState.SUSPENDED
    assert "on_suspend" in agent.log
    held = len([line for line in agent.log if line.startswith("step")])
    await asyncio.sleep(0.12)
    assert len([line for line in agent.log if line.startswith("step")]) == held

    assert await kernel.resume(proc)
    await kernel.wait(proc, timeout=5)
    assert "on_resume" in agent.log
    assert proc.exit_status is ExitStatus.DONE


@pytest.mark.asyncio
async def test_a_graceful_stop_lands_and_a_forced_one_does_not(kernel):
    graceful = Steps(name="graceful", steps=50, sleep=0.02)
    gp = await kernel.spawn(graceful, "x")
    await asyncio.sleep(0.05)
    await kernel.stop(gp)
    await kernel.wait(gp, timeout=5)

    forced = Steps(name="forced", steps=50, sleep=0.02)
    fp = await kernel.spawn(forced, "x")
    await asyncio.sleep(0.05)
    await kernel.stop(fp, force=True, reason="no time")
    await kernel.wait(fp, timeout=5)

    assert any(line.startswith("on_land") for line in graceful.log)
    assert not any(line.startswith("on_land") for line in forced.log)
    # Both still exit, and both still run on_exit: one path out, always.
    assert gp.exit_status is fp.exit_status is ExitStatus.CANCELLED
    assert any(line.startswith("on_exit") for line in forced.log)


@pytest.mark.asyncio
async def test_a_stronger_signal_replaces_a_weaker_one(kernel):
    agent = Steps(steps=50, sleep=0.02)
    proc = await kernel.spawn(agent, "x")
    await asyncio.sleep(0.03)
    await kernel.suspend(proc)
    await kernel.stop(proc, reason="stop wins")
    await kernel.wait(proc, timeout=5)
    assert proc.exit_status is ExitStatus.CANCELLED


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_parent_collects_children_from_its_mailbox_without_polling(kernel):
    """The kernel posts a final report to the parent, so waiting is `recv`, not a loop."""

    class Parent(Steps):
        def __init__(self, count):
            super().__init__(name="parent", steps=0)
            self.count = count
            self.reports: list[ReportEnvelope] = []

        async def __call__(self, task, files=None, ctx=None, **kwargs):
            children = [
                await kernel.spawn(Steps(name=f"kid{i}", steps=1), f"sub {i}",
                                   parent=self.proc)
                for i in range(self.count)
            ]
            pending = {child.pid for child in children}
            while pending:
                envelope = await self.proc.recv(timeout=5)
                assert envelope is not None
                if isinstance(envelope, ReportEnvelope) and envelope.final:
                    self.reports.append(envelope)
                    pending.discard(envelope.sender)
            return len(self.reports)

    agent = Parent(3)
    proc = await kernel.spawn(agent, "orchestrate")
    assert await kernel.wait(proc, timeout=10) == 3
    assert all(report.exit_status == "done" for report in agent.reports)


@pytest.mark.asyncio
async def test_escalation_is_a_report_and_a_reply_and_nothing_else(kernel):
    class Blocked(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            return await self.proc.ask_parent("which option?", timeout=5)

    class Boss(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            child = await kernel.spawn(Blocked(name="blocked"), "go", parent=self.proc)
            while True:
                envelope = await self.proc.recv(timeout=5)
                assert envelope is not None
                if isinstance(envelope, ReportEnvelope) and envelope.blocked:
                    await kernel.reply(envelope.sender, "option B")
                elif isinstance(envelope, ReportEnvelope) and envelope.final:
                    return child.last_result

    proc = await kernel.spawn(Boss(name="boss"), "run")
    assert await kernel.wait(proc, timeout=10) == "option B"


@pytest.mark.asyncio
async def test_a_parent_that_exits_reaps_what_it_dispatched(kernel):
    class Abandoner(Steps):
        def __init__(self):
            super().__init__(name="abandoner", steps=0)
            self.child = None

        async def __call__(self, task, files=None, ctx=None, **kwargs):
            self.child = await kernel.spawn(
                Steps(name="orphan", steps=100, sleep=0.05), "forever", parent=self.proc
            )
            return "left"

    agent = Abandoner()
    proc = await kernel.spawn(agent, "x")
    await kernel.wait(proc, timeout=5)
    await asyncio.sleep(0.1)
    assert agent.child.exited
    assert agent.child.exit_status is ExitStatus.CANCELLED


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_subscriber_registers_idle_and_spends_one_turn_per_event(kernel):
    agent = Steps(name="watcher", steps=1)
    proc = await kernel.spawn(agent, "watch deployments", topics=["deploy"])
    await asyncio.sleep(0.05)

    # Idle, not finished, and no turn spent waiting for work that has not arrived.
    assert proc.state is ProcessState.IDLE
    assert proc.turns == 0

    assert await kernel.publish("deploy", "started", {"service": "api"}) == 1
    await asyncio.sleep(0.15)
    assert await kernel.publish("deploy", "finished", {"service": "api"}) == 1
    await asyncio.sleep(0.15)

    assert proc.turns == 2
    assert proc.state is ProcessState.IDLE


@pytest.mark.asyncio
async def test_a_topic_edge_dies_with_its_subscriber(kernel):
    agent = Steps(name="watcher", steps=1)
    proc = await kernel.spawn(agent, "brief", topics=["deploy"])
    await asyncio.sleep(0.05)
    await kernel.stop(proc, force=True)
    await kernel.wait(proc, timeout=5)
    assert await kernel.publish("deploy", "started") == 0


@pytest.mark.asyncio
async def test_a_message_to_a_running_process_is_delivered_at_the_safe_point(kernel):
    agent = Steps(steps=4, sleep=0.03)
    proc = await kernel.spawn(agent, "x")
    await kernel.send(proc, TaskEnvelope(task="an aside"))
    await kernel.wait(proc, timeout=5)
    # RUNNING + message → on_event. IDLE + message → the next turn. One rule each.
    assert [type(event).__name__ for event in agent.events] == ["TaskEnvelope"]


# ---------------------------------------------------------------------------
# What observers are told
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def observed(monkeypatch):
    """Capture broadcasts instead of letting them reach the hook registry.

    Via `events.broadcast`, which is the seam every one of these goes through, so a
    firing site that stops calling it fails here rather than going quiet.
    """
    from agentevolver.agent.loop import events as bus

    seen: list = []

    async def broadcast(event, payload=None, *, ctx=None):
        seen.append((event, payload or {}))

    monkeypatch.setattr(bus.events, "broadcast", broadcast)
    return seen


@pytest.mark.asyncio
async def test_a_root_process_opens_a_session_and_a_child_opens_a_subagent(kernel, observed):
    """The two were both SESSION_START, so dispatches were counted as sessions."""
    parent = await kernel.spawn(Steps(name="parent", steps=1), "root task")
    await kernel.wait(parent, timeout=5)
    child = await kernel.spawn(Steps(name="child", steps=1), "sub task", parent=parent)
    await kernel.wait(child, timeout=5)

    def names(pid):
        return [event.name for event, body in observed if body.get("task_id") == pid]

    assert names(parent.pid) == ["USER_PROMPT_SUBMIT", "SESSION_START",
                                 "TASK_COMPLETED", "SESSION_END"]
    assert names(child.pid) == ["SUBAGENT_START", "TASK_COMPLETED", "SUBAGENT_STOP"]


@pytest.mark.asyncio
async def test_a_suspend_and_a_resume_are_visible_from_outside_the_process(kernel, observed):
    """The kernel had the phase and nothing outside could see it.

    Without this a held process reads in a trace exactly like one that stopped
    producing steps on its own.
    """
    agent = Steps(steps=6, sleep=0.02)
    proc = await kernel.spawn(agent, "x")
    await asyncio.sleep(0.03)
    await kernel.suspend(proc)
    await asyncio.sleep(0.05)
    assert proc.state is ProcessState.SUSPENDED
    await kernel.resume(proc)
    await kernel.wait(proc, timeout=5)

    held = [event.name for event, _ in observed
            if event.name in ("ON_SUSPEND", "ON_RESUME")]
    assert held == ["ON_SUSPEND", "ON_RESUME"]
    # The agent's own phase hooks still ran; the broadcast is additional, not a
    # replacement — one is for the process, the other for everyone watching it.
    assert "on_suspend" in agent.log and "on_resume" in agent.log


@pytest.mark.asyncio
async def test_a_scoped_publish_reaches_processes_that_subscribed_by_name(kernel):
    """Subscribe and publish must scope the topic the same way, or nothing is delivered.

    `spawn(topics=[...])` registered the raw name while `publish_scoped` looked up
    `{root}::{name}`, so the two never matched. It failed silently and in the worst
    possible shape: the fan-out reported success with a count of zero, and every
    resident subscriber sat IDLE forever waiting for an event delivered to nobody.
    Caught on a live run — four subscribers registered and
    `📡 publish … → 0 subscriber(s)`.
    """
    ctx = SimpleNamespace(id="sess-1", extra={"root_session_id": "sess-1"})
    agents = [Steps(name=f"sub{i}", steps=1) for i in range(3)]
    for agent in agents:
        await kernel.spawn(agent, "standing brief", ctx=ctx, topics=["deployment.ready"])
    await asyncio.sleep(0.05)

    delivered, name, _ = await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {"url": "http://site"}, ctx=ctx,
    )
    assert name == "sess-1::deployment.ready"
    assert delivered == 3, "a scoped publish must reach every process that subscribed"


@pytest.mark.asyncio
async def test_a_topic_still_pairs_up_without_a_session(kernel):
    """A bare kernel — a test, a script — keeps the plain name on both sides."""
    agent = Steps(name="watcher", steps=1)
    await kernel.spawn(agent, "brief", topics=["plain"])
    await asyncio.sleep(0.05)
    assert await kernel.publish("plain", "started") == 1


@pytest.mark.asyncio
async def test_a_resident_keeps_its_standing_brief_however_it_is_woken(kernel):
    """A subscriber's brief is its identity, and every turn needs it.

    `_input_text` returned `envelope.task` alone for a TaskEnvelope, so a resident woken
    by `send_message` — as opposed to by a published event — lost the standing brief it
    was spawned with. In the website demo that brief carries the participant's assigned
    persona, and the subscriber answered "NO ASSIGNED CONTEXT": a failure that reads like
    the parent forgot to assign one rather than like the kernel discarding it.
    """
    seen: list[str] = []

    class Panelist(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            seen.append(task)
            await self.proc.gate()
            return "ok"

    ctx = SimpleNamespace(id="sess-1", extra={"root_session_id": "sess-1"})
    proc = await kernel.spawn(
        Panelist(name="panelist"), "You are participant_01. Report as that resident.",
        ctx=ctx, topics=["deployment.ready"],
    )
    await asyncio.sleep(0.05)

    await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {"url": "http://site"}, ctx=ctx
    )
    await asyncio.sleep(0.15)
    await kernel.send(proc, TaskEnvelope(task="Release 2 is up; take another look."))
    await asyncio.sleep(0.15)

    assert len(seen) == 2, seen
    for turn in seen:
        assert "participant_01" in turn, f"the brief was dropped: {turn!r}"
    assert "http://site" in seen[0]
    assert "Release 2" in seen[1]


@pytest.mark.asyncio
async def test_a_one_shot_dispatch_is_not_prefixed_with_anything(kernel):
    """Only a resident has a brief; a plain dispatch must read exactly as it was sent."""
    agent = Steps(name="worker", steps=1)
    proc = await kernel.spawn(agent, "do exactly this")
    assert await kernel.wait(proc, timeout=5) == "worker:do exactly this"
