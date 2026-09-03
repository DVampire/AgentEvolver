"""Each co-design subscriber gets its own session, and still hears the same topic.

`start_subscriber` passed the builder's own context to `kernel.spawn`, so all four
subscribers — three participants and the acceptance worker — carried the builder's
session id. A session id is what `BrowserEnvironment` keys a browser TAB on, so the four
of them, woken by the same `deployment.ready` event, drove ONE page at the same time.

The symptom read as a missing capability rather than a collision. Each participant had
eleven browser actions in its roster and reported, in its own words, "the browser
remained on about:blank, and no browser navigation/interaction capability was available"
— while another had just navigated that same tab away. A whole demo run produced zero
grounded product feedback and, downstream of that, no evolution: the builder had nothing
to learn a capability gap from.

The dispatch path never had this: `CapabilityRouter._child_context` mints a fresh
`AgentContext` per child, and a fresh `AgentContext` carries a fresh id.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.agent.types import AgentContext
from agentevolver.runtime.kernel import Kernel


class Resident:
    """A process that stays alive long enough to be published to."""

    def __init__(self, name="resident"):
        self.name = name
        self.events = []

    async def __call__(self, task, files=None, ctx=None, **kwargs):
        for _ in range(40):
            await self.proc.gate()
            await asyncio.sleep(0.02)
        return "done"

    async def on_event(self, envelope, proc):
        self.events.append(envelope)


@pytest_asyncio.fixture
async def kernel():
    instance = Kernel()
    try:
        yield instance
    finally:
        await instance.shutdown(timeout=5)


async def _panel(kernel, count=4):
    """A parent and `count` subscribers, built the way `start_subscriber` builds them."""
    parent_ctx = SimpleNamespace(
        id="builder-session", extra={"root_session_id": "builder-session"}
    )
    parent = await kernel.spawn(Resident("builder"), "build", ctx=parent_ctx)
    subscribers = []
    for index in range(count):
        child_ctx = AgentContext(
            name=f"participant_{index}",
            parent_session_id="builder-session",
            extra={"root_session_id": "builder-session"},
        )
        subscribers.append(await kernel.spawn(
            Resident(f"participant_{index}"), "your persona",
            ctx=child_ctx, parent=parent, topics=["deployment.ready"],
        ))
    await asyncio.sleep(0.1)
    return parent_ctx, parent, subscribers


@pytest.mark.asyncio
async def test_every_subscriber_gets_its_own_session(kernel):
    """One session id per subscriber, and none of them the parent's.

    Asserted on the ids rather than on the browser, because the browser is only the
    first thing that breaks when they collide: screenshots, per-session step counters
    and memory are all keyed the same way.
    """
    parent_ctx, parent, subscribers = await _panel(kernel)
    sessions = [proc.session_id for proc in subscribers]
    assert len(set(sessions)) == len(subscribers), sessions
    assert parent.session_id not in sessions


@pytest.mark.asyncio
async def test_separate_sessions_still_hear_one_scoped_topic(kernel):
    """The isolation must not cost them the event.

    Scoping reads `root_session_id`, which each subscriber inherits from the parent, so
    a per-subscriber session id changes the browser tab and nothing about delivery.
    """
    parent_ctx, _parent, subscribers = await _panel(kernel)
    delivered, name, _envelope = await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {"url": "http://site"}, ctx=parent_ctx,
    )
    assert name == "builder-session::deployment.ready"
    assert delivered == len(subscribers)


@pytest.mark.asyncio
async def test_a_subscriber_keeps_the_parent_s_topic_scope_not_its_own(kernel):
    """Without `root_session_id` a subscriber would scope to its own id and hear nothing.

    That is the failure the previous shared-context arrangement accidentally avoided,
    and the reason isolation had to carry the scope across explicitly.
    """
    parent_ctx = SimpleNamespace(
        id="builder-session", extra={"root_session_id": "builder-session"}
    )
    parent = await kernel.spawn(Resident("builder"), "build", ctx=parent_ctx)
    stray = AgentContext(name="stray", parent_session_id="builder-session", extra={})
    await kernel.spawn(
        Resident("stray"), "brief", ctx=stray, parent=parent, topics=["deployment.ready"],
    )
    await asyncio.sleep(0.05)
    delivered, _name, _envelope = await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {}, ctx=parent_ctx,
    )
    assert delivered == 0, "a subscriber that scopes to its own session hears nothing"
