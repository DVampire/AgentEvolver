"""A dispatched child can be given the conversation behind its task.

A child starts with a fresh session and sees none of its parent's work, so everything it
needs has to survive the trip inside one `task` string. That is the defect attachments
already fixed on the file side — without them a child got only the orchestrator's
paraphrase of a document the orchestrator was handed in full — and this is the
conversation half of it. The parent read five files and ruled out three approaches;
re-typing that accurately into a brief is work it will do imperfectly every time.

`fork` is off by default, and that is not timidity: a fresh worker on a self-contained
job reads faster without a history that is not about it. The tests below pin both
directions, and pin the two things a fork must not do — write another agent's turns into
this child's log, or grow without bound.
"""

from __future__ import annotations

import pytest

from agentevolver.agent.types import _INHERITED_CONTEXT_MAX
from agentevolver.protocol.server import protocol_manager
from agentevolver.trace.types import agent_call_event, agent_start_event


class _Child:
    name = "code_agent"


class _Parent:
    def __init__(self, session: str = "parent-session"):
        self.id = session
        self.extra = {"workspace_root": "/ws"}


class _Agent:
    """`_get_inherited_context` unbound, so this exercises the method rather than a
    rebuild of the agent's whole construction path."""

    from agentevolver.agent.types import Agent

    _get_inherited_context = Agent._get_inherited_context
    name = "code_agent"


def _parent_log(turns: int = 2, reasoning: str = "ruled out the parser"):
    events = [agent_start_event("parent-session", "t", "meta_agent", "investigate")]
    for step in range(1, turns + 1):
        events.append(agent_call_event("parent-session", "t", "meta_agent", step,
                                       reasoning=f"{reasoning} ({step})"))
    for position, event in enumerate(events):
        event.seq_no = position
    return events


@pytest.fixture
def parent_log(monkeypatch):
    """Serve one parent history, and record which session was asked for."""
    asked: list = []
    log = {"events": _parent_log()}

    from agentevolver.trace import trace_manager

    def events(session_id, *args, **kwargs):
        asked.append(session_id)
        return log["events"] if session_id == "parent-session" else []

    monkeypatch.setattr(trace_manager, "events", events)
    return asked, log


# --------------------------------------------------------------------------- #
# What the brief records
# --------------------------------------------------------------------------- #
def test_a_forked_child_records_the_session_it_was_forked_from():
    _, ctx = protocol_manager.child_brief(_Child(), "go", parent_ctx=_Parent(), fork=True)
    assert ctx.extra.get("forked_from") == "parent-session"


def test_an_ordinary_child_inherits_no_conversation():
    """The default. A child that did not ask for its parent's history must not get it —
    it is tokens on every step about work that is not its own."""
    _, ctx = protocol_manager.child_brief(_Child(), "go", parent_ctx=_Parent())
    assert "forked_from" not in ctx.extra


def test_a_fork_without_a_parent_context_records_nothing():
    """There is no session to read, and inventing one would point at another run's log."""
    _, ctx = protocol_manager.child_brief(_Child(), "go", fork=True)
    assert "forked_from" not in ctx.extra


# --------------------------------------------------------------------------- #
# What the child is shown
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_child_is_shown_its_parents_turns(parent_log):
    asked, _ = parent_log
    ctx = type("Ctx", (), {"id": "child", "extra": {"forked_from": "parent-session"}})()

    modules = await _Agent()._get_inherited_context(ctx=ctx)

    assert "ruled out the parser" in modules["inherited_context"]
    assert asked == ["parent-session"], f"read the wrong session: {asked}"


@pytest.mark.asyncio
async def test_the_child_is_told_the_history_is_not_its_own():
    """Read as its own, the child reports work it never did and repeats none of it."""
    from agentevolver.trace import trace_manager

    ctx = type("Ctx", (), {"id": "child", "extra": {"forked_from": "parent-session"}})()
    original, events = trace_manager.events, _parent_log()
    trace_manager.events = lambda session_id, *a, **k: events
    try:
        modules = await _Agent()._get_inherited_context(ctx=ctx)
    finally:
        trace_manager.events = original

    body = modules["inherited_context"]
    assert "not your own history" in body and "did not take these actions" in body


@pytest.mark.asyncio
async def test_an_unforked_child_reads_no_log_at_all(parent_log):
    """Not merely an empty result — the projection is skipped, so a fork nobody asked
    for costs nothing."""
    asked, _ = parent_log
    ctx = type("Ctx", (), {"id": "child", "extra": {}})()

    assert await _Agent()._get_inherited_context(ctx=ctx) == {}
    assert asked == []


@pytest.mark.asyncio
async def test_an_inherited_history_is_bounded_from_the_tail(parent_log):
    """A parent's history has no natural size and this rides in the prompt every step.

    Kept from the tail because the recent turns are the ones the parent's decision
    rested on — and because the pressure guard downstream may only shrink tool results,
    so it would have nothing to take from this.
    """
    _, log = parent_log
    log["events"] = _parent_log(turns=400, reasoning="x" * 200)

    body = (await _Agent()._get_inherited_context(
        ctx=type("Ctx", (), {"id": "c", "extra": {"forked_from": "parent-session"}})()
    ))["inherited_context"]

    assert len(body) < _INHERITED_CONTEXT_MAX * 1.2
    assert "earlier turns omitted" in body
    assert "(400)" in body, "kept the head instead of the tail"


# --------------------------------------------------------------------------- #
# What a fork must not do
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_forked_child_does_not_write_its_parents_turns_into_its_own_log(parent_log):
    """The child's trace would otherwise claim turns another agent took, and every
    training sample projected from it would be attributed to the wrong policy."""
    emitted: list = []
    from agentevolver.trace import trace_manager

    original = getattr(trace_manager, "emit", None)
    trace_manager.emit = lambda event, *a, **k: emitted.append(event)
    try:
        await _Agent()._get_inherited_context(
            ctx=type("Ctx", (), {"id": "child", "extra": {"forked_from": "parent-session"}})()
        )
    finally:
        if original is not None:
            trace_manager.emit = original

    assert emitted == [], "a fork wrote into the child's log"


def test_the_schema_offers_fork_to_the_model():
    """A capability the schema does not mention cannot be reached: the dispatch schema is
    `strict` with `additionalProperties: false`, so an argument missing from it is
    rejected rather than ignored."""
    from agentevolver.agent.server import AgentManagerServer

    properties = AgentManagerServer._dispatch_parameters()["properties"]
    assert "fork" in properties
    assert properties["fork"]["type"] == "boolean"


def test_the_task_description_no_longer_claims_to_be_everything():
    """It said the sub-agent receives only the task. With `fork` that is conditional, and
    a schema that describes the old behaviour teaches the model the wrong thing."""
    from agentevolver.agent.server import AgentManagerServer

    task = AgentManagerServer._dispatch_parameters()["properties"]["task"]["description"]
    assert "fork" in task


def test_the_prompt_renders_the_slot_only_when_there_is_something_in_it():
    """26 templates share this module; an unconditional slot would put an empty
    `<inherited-context>` into every prompt in the repository."""
    from pathlib import Path

    module = (Path(__file__).resolve().parents[1]
              / "agentevolver" / "prompt" / "module" / "agent_context.html").read_text()
    assert "{% if inherited_context %}" in module
    assert module.index("{% if inherited_context %}") < module.index("{{ inherited_context }}")
