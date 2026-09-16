"""Every collaboration shape this system claims to support, built from the primitives.

An assurance that "the runtime can express any topology" is worth what it can be checked
against, so each pattern in the standard taxonomy is constructed here from `spawn`,
`send`, `publish`, `assign` and `wait` — and the two that are deliberately absent are
recorded as absent rather than left to be discovered.

The vocabulary is the usual one: message exchange patterns from messaging middleware,
socket roles from ZeroMQ, supervision from the actor model.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.agent.types import AgentContext
from agentevolver.runtime.envelopes import TaskEnvelope
from agentevolver.runtime.kernel import Kernel
from agentevolver.runtime.modes import InteractionMode


class Node:
    """A participant that records what it was asked to do."""

    def __init__(self, name="node", steps=1, sleep=0.03):
        self.name = name
        self.steps = steps
        self.sleep = sleep
        self.turns: list = []
        self.events: list = []

    async def __call__(self, task, files=None, ctx=None, **kwargs):
        self.turns.append(str(task))
        for _ in range(self.steps):
            await self.proc.gate()
            await asyncio.sleep(self.sleep)
        return f"{self.name}:done"

    async def on_event(self, envelope, proc):
        self.events.append(envelope)


@pytest_asyncio.fixture
async def kernel():
    instance = Kernel()
    try:
        yield instance
    finally:
        await instance.shutdown(timeout=5)


def _root(session="s1"):
    return SimpleNamespace(id=session, extra={"root_session_id": session})


def _child_ctx(name, session="s1"):
    """A context of a child's own, carrying the topic scope of its task tree."""
    return AgentContext(name=name, extra={"root_session_id": session})


# ---------------------------------------------------------------------------
# Dispatch topologies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_star_one_orchestrator_many_responders(kernel):
    """Hub and spoke. Request/reply, fanned out and collected."""
    workers = [Node(name=f"w{index}") for index in range(4)]
    # SERVICE for the same reason the pipeline's head is one: an orchestrator that exits
    # reaps its children, and a test that waits on them must not race that.
    hub = await kernel.spawn(
        Node("hub", steps=2), "coordinate", mode=InteractionMode.SERVICE, ctx=_root(),
    )
    procs = [
        await kernel.spawn(w, f"job {index}", parent=hub,
                           mode=InteractionMode.RESPONDER)
        for index, w in enumerate(workers)
    ]
    results = await asyncio.gather(*[kernel.wait(p, timeout=5) for p in procs])
    assert results == [f"w{index}:done" for index in range(4)]
    assert [w.turns for w in workers] == [[f"job {index}"] for index in range(4)]


@pytest.mark.asyncio
async def test_tree_orchestrators_nest(kernel):
    """A child that dispatches its own children. Depth is not special-cased."""
    leaf = Node("leaf")
    middle = Node("middle", steps=2)
    root = await kernel.spawn(
        Node("root", steps=2), "top", mode=InteractionMode.SERVICE, ctx=_root(),
    )
    mid_proc = await kernel.spawn(
        middle, "middle work", mode=InteractionMode.SERVICE, parent=root,
    )
    leaf_proc = await kernel.spawn(leaf, "leaf work", parent=mid_proc)

    assert await kernel.wait(leaf_proc, timeout=5) == "leaf:done"
    assert leaf_proc.parent_pid == mid_proc.pid
    assert mid_proc.parent_pid == root.pid
    assert [p.pid for p in kernel.children(mid_proc)] == [leaf_proc.pid]


@pytest.mark.asyncio
async def test_pipeline_each_stage_dispatches_the_next(kernel):
    """A chain, where each stage outlives the one it started.

    That is not a detail of the test: a dispatching parent must outlive its child,
    because `_exit` reaps whatever a process spawned. An intermediate stage that answers
    and exits takes the rest of the chain down with it, so every stage but the last is a
    SERVICE — it parks rather than finishing, which is what "waiting on the next stage"
    means in process terms.
    """
    stages = [Node(f"stage{index}", steps=2) for index in range(3)]
    parent = await kernel.spawn(
        Node("source", steps=2), "start", mode=InteractionMode.SERVICE, ctx=_root(),
    )
    for index, stage in enumerate(stages):
        last = index == len(stages) - 1
        parent = await kernel.spawn(
            stage, f"stage {index} input", parent=parent,
            mode=InteractionMode.RESPONDER if last else InteractionMode.SERVICE,
        )
    assert await kernel.wait(parent, timeout=5) == "stage2:done"
    assert [s.turns[0] for s in stages] == [f"stage {i} input" for i in range(3)]


@pytest.mark.asyncio
async def test_a_parent_that_exits_takes_its_children_with_it(kernel):
    """The property the pipeline has to respect, stated on its own.

    Supervision, not a leak: nobody is left to collect a child whose parent is gone. It
    is also the sharpest edge in composing topologies — an orchestrator that answers and
    exits while its children are still working destroys their work, and the failure
    reads as a child that never finished.
    """
    orphan = Node("orphan", steps=60, sleep=0.02)
    parent = await kernel.spawn(Node("brief", steps=1), "answer fast", ctx=_root())
    child = await kernel.spawn(orphan, "long job", parent=parent)

    assert await kernel.wait(parent, timeout=5) == "brief:done"
    await asyncio.sleep(0.1)
    assert not child.alive, "a child outliving its parent would have no collector"
    assert len(orphan.turns) == 1 and orphan.turns[0] == "long job"


@pytest.mark.asyncio
async def test_scatter_gather_collects_children_as_they_finish(kernel):
    """Fan out, then collect. A parent need not block on each in turn.

    The kernel posts a final report to the parent's mailbox when a child exits — the
    SIGCHLD analogue — so the collecting half needs no mechanism of its own.
    """
    collector = Node("collector", steps=20, sleep=0.02)
    parent = await kernel.spawn(
        collector, "gather", mode=InteractionMode.SERVICE, ctx=_root(),
    )
    for index in range(3):
        await kernel.spawn(Node(f"leaf{index}"), f"part {index}", parent=parent)
    await asyncio.sleep(0.45)
    reports = [e for e in collector.events if getattr(e, "final", False)]
    assert len(reports) == 3, [type(e).__name__ for e in collector.events]


# ---------------------------------------------------------------------------
# Bus topologies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_one_publisher_many_subscribers(kernel):
    """Publish/subscribe. The publisher never learns who listened."""
    root = _root()
    hub = await kernel.spawn(Node("hub", steps=12), "publish later",
                             mode=InteractionMode.SERVICE, ctx=root)
    listeners = [Node(f"sub{index}") for index in range(3)]
    for index, listener in enumerate(listeners):
        await kernel.spawn(
            listener, f"standing brief {index}", mode=InteractionMode.SUBSCRIBER,
            ctx=_child_ctx(f"sub{index}"), parent=hub, topics=["release"],
        )
    await asyncio.sleep(0.05)

    delivered, name, _envelope = await kernel.publish_scoped(
        "release", "ready", {"url": "http://site"}, ctx=root,
    )
    assert delivered == 3 and name == "s1::release"
    await asyncio.sleep(0.2)
    for index, listener in enumerate(listeners):
        assert listener.turns, f"sub{index} never woke"
        assert f"standing brief {index}" in listener.turns[0]
        assert "http://site" in listener.turns[0]


@pytest.mark.asyncio
async def test_bus_many_publishers_reach_the_same_listeners(kernel):
    """Many-to-many. Any process in the task tree may publish."""
    root = _root()
    hub = await kernel.spawn(Node("hub", steps=14), "x",
                             mode=InteractionMode.SERVICE, ctx=root)
    listener = Node("listener", steps=2)
    await kernel.spawn(
        listener, "brief", mode=InteractionMode.SUBSCRIBER,
        ctx=_child_ctx("listener"), parent=hub, topics=["bus"],
    )
    speaker_ctx = _child_ctx("speaker")
    await kernel.spawn(Node("speaker", steps=10), "y",
                       mode=InteractionMode.SERVICE, ctx=speaker_ctx, parent=hub)
    await asyncio.sleep(0.05)

    # Once from the hub's context, once from a sibling's. Same topic, same listener.
    assert (await kernel.publish_scoped("bus", "a", {}, ctx=root))[0] == 1
    await asyncio.sleep(0.15)
    assert (await kernel.publish_scoped("bus", "b", {}, ctx=speaker_ctx))[0] == 1
    await asyncio.sleep(0.15)
    assert len(listener.turns) == 2, listener.turns


@pytest.mark.asyncio
async def test_mesh_peers_reach_each_other_over_the_bus(kernel):
    """Peer to peer, without a parent edge between the peers.

    Each subscribes to a topic named after itself, so a sibling addresses it without
    holding its pid and without any authority relation. This is why closing pid-based
    peer addressing costs no expressiveness.
    """
    root = _root()
    hub = await kernel.spawn(Node("hub", steps=14), "x",
                             mode=InteractionMode.SERVICE, ctx=root)
    a, b = Node("A", steps=2), Node("B", steps=2)
    ctx_a, ctx_b = _child_ctx("A"), _child_ctx("B")
    proc_a = await kernel.spawn(a, "I am A", mode=InteractionMode.SUBSCRIBER,
                                ctx=ctx_a, parent=hub, topics=["to-A"])
    proc_b = await kernel.spawn(b, "I am B", mode=InteractionMode.SUBSCRIBER,
                                ctx=ctx_b, parent=hub, topics=["to-B"])
    assert proc_a.parent_pid == proc_b.parent_pid, "siblings, not parent and child"
    await asyncio.sleep(0.05)

    assert (await kernel.publish_scoped("to-B", "ping", {"from": "A"}, ctx=ctx_a))[0] == 1
    await asyncio.sleep(0.2)
    assert b.turns and "from: A" in b.turns[0]
    assert not a.turns, "a peer message must not come back to its sender"


# ---------------------------------------------------------------------------
# Worker pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_competing_consumers_spread_work_across_a_pool(kernel):
    """PUSH/PULL. One queue, N interchangeable workers, one job each.

    `publish` cannot express this — it fans out, so every worker would do the whole
    job — and `send` cannot either, because naming the worker is dispatch rather than a
    pool. `assign` is the delivery discipline that asks who is free.
    """
    root = _root()
    hub = await kernel.spawn(Node("hub", steps=20, sleep=0.02), "x",
                             mode=InteractionMode.SERVICE, ctx=root)
    pool = [Node(f"w{index}", steps=1) for index in range(3)]
    for index, worker in enumerate(pool):
        await kernel.spawn(
            worker, "waiting", mode=InteractionMode.SUBSCRIBER,
            ctx=_child_ctx(f"w{index}"), parent=hub, topics=["queue"],
        )
    await asyncio.sleep(0.05)

    handled = []
    for index in range(3):
        pid = await kernel.assign("queue", f"job {index}", ctx=root)
        assert pid, f"job {index} went nowhere"
        handled.append(pid)
        await asyncio.sleep(0.12)
    await asyncio.sleep(0.2)

    assert len(set(handled)) == 3, "three jobs must reach three different workers"
    assert sorted(len(w.turns) for w in pool) == [1, 1, 1], [w.turns for w in pool]


@pytest.mark.asyncio
async def test_assigning_with_nobody_listening_says_so(kernel):
    """A queue with no consumers returns nothing rather than dropping work silently."""
    assert await kernel.assign("nobody", "work", ctx=_root()) == ""


# ---------------------------------------------------------------------------
# Along the parent edge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_child_can_ask_its_parent_and_be_answered(kernel):
    """Blocking request/reply, upward. A blocked child IS a process awaiting a message."""
    answer: dict = {}

    class Asker(Node):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            await self.proc.gate()
            answer["said"] = await self.proc.ask_parent("which way?", timeout=5)
            return "asked"

    parent = await kernel.spawn(Node("parent", steps=20, sleep=0.02), "x",
                              mode=InteractionMode.SERVICE, ctx=_root())
    child = await kernel.spawn(Asker("asker"), "decide", parent=parent)
    await asyncio.sleep(0.1)
    assert await kernel.reply(child, "left")
    assert await kernel.wait(child, timeout=5) == "asked"
    assert answer["said"] == "left"


@pytest.mark.asyncio
async def test_a_service_holds_a_conversation_by_pid(kernel):
    """Asynchronous request/reply. Whoever holds the pid keeps the exchange going."""
    worker = Node("service", steps=1)
    proc = await kernel.spawn(worker, "first", mode=InteractionMode.SERVICE, ctx=_root())
    await asyncio.sleep(0.12)
    for message in ("second", "third"):
        assert await kernel.send(proc, TaskEnvelope(task=message))
        await asyncio.sleep(0.12)
    assert [t.strip() for t in worker.turns] == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# What is deliberately not expressible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_dispatch_graph_stays_acyclic(kernel):
    """One parent each, by construction. A cycle in supervision has no meaning.

    Cycles between participants are expressible on the bus, where they belong: a topic
    edge carries no lifecycle, so A answering B answering A supervises nothing.
    """
    root = await kernel.spawn(
        Node("root", steps=2), "x", mode=InteractionMode.SERVICE, ctx=_root(),
    )
    child = await kernel.spawn(
        Node("child", steps=2), "y", mode=InteractionMode.SERVICE, parent=root,
    )
    grandchild = await kernel.spawn(Node("grand", steps=2), "z", parent=child)

    seen, cursor = set(), grandchild
    while cursor is not None and cursor.parent_pid:
        assert cursor.pid not in seen, "a cycle in the process tree"
        seen.add(cursor.pid)
        cursor = kernel.get(cursor.parent_pid)
    assert cursor is not None and cursor.pid == root.pid


@pytest.mark.asyncio
async def test_a_peer_pid_is_reachable_but_not_handed_out(kernel):
    """`send` takes any pid; discovery is what makes peer addressing a decision.

    Both halves exist at this layer — `list(session_id=...)` enumerates a session and
    `send` does not check kinship — so the kernel does not forbid it. What no capability
    does is hand one process another's pid, which keeps addressing a deliberate act
    rather than an accident.
    """
    root = _root()
    a = await kernel.spawn(
        Node("A", steps=8), "x", mode=InteractionMode.SERVICE, ctx=root,
    )
    b = Node("B", steps=8)
    proc_b = await kernel.spawn(b, "y", mode=InteractionMode.SERVICE, ctx=root)
    await asyncio.sleep(0.05)

    session = [p for p in kernel.list(session_id=a.session_id) if p.pid != a.pid]
    assert proc_b.pid in {p.pid for p in session}, "a session is enumerable"
    assert await kernel.send(proc_b, TaskEnvelope(task="direct")), "and reachable"
    await asyncio.sleep(0.1)
    assert any(getattr(e, "task", "") == "direct" for e in b.events), (
        "delivered at the safe point of a running process, as any message is"
    )
