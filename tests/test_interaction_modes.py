"""A process declares how work reaches it, and the kernel derives the rest.

"Subscriber" was not a thing this runtime knew. It was `resident=True` plus
`topics=[...]` plus `start_idle=True` plus a context of its own plus a standing brief
that leads every turn — five facts assembled by hand at each call site, where getting
one wrong raised nothing and produced a process that looked spawned and did nothing.
Three separate defects came from exactly that, and each cost a whole run:

- subscribers spawned with the parent's context shared one browser tab, so each read a
  page another had just navigated away from and reported "no browser capability";
- a standing brief was dropped on the direct-message path, so a participant answered
  "NO ASSIGNED CONTEXT" about a persona it had been handed;
- a topic registered unscoped while the publisher looked up a scoped name, so every
  fan-out reached nobody and reported success.

The names are endpoint roles rather than conversation names, following the one decision
worth borrowing from ZeroMQ: there is no PUBSUB socket, only PUB and SUB. An agent
labelled "broadcast" is either the publisher or a listener and the runtime cannot tell
which — a participant needs a role, not a topology.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.runtime.kernel import Kernel
from agentevolver.runtime.modes import (
    InteractionMode,
    check_topics,
    infer,
    lifecycle,
)
from agentevolver.runtime.states import ProcessState


class Stepper:
    """A process with no model: N awaited steps through the safe point."""

    def __init__(self, name="stepper", steps=3):
        self.name = name
        self.steps = steps
        self.tasks: list = []

    async def __call__(self, task, files=None, ctx=None, **kwargs):
        self.tasks.append(task)
        for _ in range(self.steps):
            await self.proc.gate()
            await asyncio.sleep(0.02)
        return f"{self.name}:done"

    async def on_event(self, envelope, proc):
        pass


@pytest_asyncio.fixture
async def kernel():
    instance = Kernel()
    try:
        yield instance
    finally:
        await instance.shutdown(timeout=5)


def _ctx(session="s1"):
    return SimpleNamespace(id=session, extra={"root_session_id": session})


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_every_mode_has_a_lifecycle():
    """Guards the guard: a mode with no entry would raise, not default quietly."""
    for mode in InteractionMode:
        assert lifecycle(mode).topics in ("required", "forbidden")


def test_an_unknown_mode_names_the_ones_that_exist():
    with pytest.raises(ValueError) as raised:
        lifecycle("gossip")
    assert "subscriber" in str(raised.value), "a rejection should list the alternatives"


@pytest.mark.parametrize(
    "resident,topics,expected",
    [
        (False, (), InteractionMode.RESPONDER),
        (True, (), InteractionMode.SERVICE),
        (True, ("deploy",), InteractionMode.SUBSCRIBER),
        (False, ("deploy",), InteractionMode.SUBSCRIBER),
    ],
)
def test_the_old_flag_combinations_map_onto_the_mode_they_meant(resident, topics, expected):
    """Both spellings stay accepted, with one implementation behind them.

    The last case is the inference the kernel already made: naming a topic implies
    residency, because a subscriber that exited could not receive the next event.
    """
    assert infer(resident, topics) is expected


# ---------------------------------------------------------------------------
# Contradictions are refused where they are made
# ---------------------------------------------------------------------------


def test_a_subscriber_without_a_topic_is_refused():
    """It would wait for an event nobody can address to it, and look fine doing so."""
    with pytest.raises(ValueError, match="nobody can send"):
        check_topics(InteractionMode.SUBSCRIBER, [])


def test_a_pid_addressed_mode_with_topics_is_refused():
    """The edge would be registered and dropped moments later when it exits."""
    with pytest.raises(ValueError, match="addressed by pid"):
        check_topics(InteractionMode.RESPONDER, ["deploy"])
    with pytest.raises(ValueError, match="addressed by pid"):
        check_topics(InteractionMode.SERVICE, ["deploy"])


@pytest.mark.asyncio
async def test_the_kernel_refuses_a_contradiction_at_spawn(kernel):
    """Refused where the mistake is made, not discovered from a run with no output."""
    with pytest.raises(ValueError):
        await kernel.spawn(Stepper(), "x", mode=InteractionMode.SUBSCRIBER)
    with pytest.raises(ValueError):
        await kernel.spawn(
            Stepper(), "x", mode=InteractionMode.RESPONDER, topics=["deploy"],
        )


# ---------------------------------------------------------------------------
# What each mode actually does
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_responder_answers_once_and_exits(kernel):
    agent = Stepper(name="worker", steps=1)
    proc = await kernel.spawn(agent, "fix the bug", mode=InteractionMode.RESPONDER)
    assert await kernel.wait(proc, timeout=5) == "worker:done"
    assert proc.state is ProcessState.EXITED
    assert proc.mode is InteractionMode.RESPONDER
    assert agent.tasks == ["fix the bug"]


@pytest.mark.asyncio
async def test_a_service_runs_its_first_task_then_waits_by_pid(kernel):
    agent = Stepper(name="helper", steps=1)
    proc = await kernel.spawn(agent, "first job", mode=InteractionMode.SERVICE)
    await asyncio.sleep(0.15)
    assert proc.state is ProcessState.IDLE, "a service parks instead of exiting"
    assert agent.tasks == ["first job"], "it runs the first task immediately"

    await kernel.send_task(proc, "second job")
    await asyncio.sleep(0.15)
    assert agent.tasks[-1].endswith("second job")


@pytest.mark.asyncio
async def test_a_subscriber_does_not_run_until_an_event_arrives(kernel):
    """The half most easily got wrong by hand.

    Running on spawn would mean acting on an event that has not happened — visiting a
    site before it is deployed. So `task` is a standing brief, and it leads every turn.
    """
    ctx = _ctx()
    agent = Stepper(name="panelist", steps=1)
    proc = await kernel.spawn(
        agent, "You are participant_01.", mode=InteractionMode.SUBSCRIBER,
        ctx=ctx, topics=["deployment.ready"],
    )
    await asyncio.sleep(0.1)
    assert proc.state is ProcessState.IDLE
    assert agent.tasks == [], "a subscriber must not run before its event"

    delivered, _name, _envelope = await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {"url": "http://site"}, ctx=ctx,
    )
    assert delivered == 1
    await asyncio.sleep(0.15)
    assert len(agent.tasks) == 1
    assert "participant_01" in agent.tasks[0], "the brief leads the turn"
    assert "http://site" in agent.tasks[0], "so does the event that woke it"


@pytest.mark.asyncio
async def test_a_listing_says_the_role_rather_than_leaving_it_to_be_inferred(kernel):
    """`ps` should read "subscriber", not two flags a reader has to recombine."""
    ctx = _ctx()
    responder = await kernel.spawn(Stepper(steps=4), "a", mode=InteractionMode.RESPONDER)
    service = await kernel.spawn(Stepper(steps=4), "b", mode=InteractionMode.SERVICE)
    subscriber = await kernel.spawn(
        Stepper(steps=4), "c", mode=InteractionMode.SUBSCRIBER,
        ctx=ctx, topics=["deploy"],
    )
    await asyncio.sleep(0.05)
    assert [proc.snapshot()["mode"] for proc in (responder, service, subscriber)] == [
        "responder", "service", "subscriber",
    ]


@pytest.mark.asyncio
async def test_spawning_without_a_mode_still_works(kernel):
    """The old spelling is still accepted; both go through one implementation."""
    ctx = _ctx()
    legacy = await kernel.spawn(
        Stepper(steps=2), "brief", ctx=ctx, topics=["deploy"],
    )
    await asyncio.sleep(0.05)
    assert legacy.mode is InteractionMode.SUBSCRIBER
    assert legacy.state is ProcessState.IDLE
