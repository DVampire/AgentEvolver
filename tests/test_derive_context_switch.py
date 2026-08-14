"""The derived-history switch, and what it does when the log cannot support it.

`derive_context` changes what every step of every agent sees, so it is off by default
and switched on per agent against a measurement. These tests pin the two things that
must hold whatever the measurement says: off is exactly the old path, and on never
silently hands the model a shorter conversation than the one that happened.
"""

import asyncio

import pytest

from agentevolver.message.types import AssistantMessage, HumanMessage, SystemMessage
from agentevolver.trace.server import TraceManager
from agentevolver.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_call_event,
    agent_start_event,
    tool_call_event,
    tool_start_event,
)


def _manager():
    manager = TraceManager.__new__(TraceManager)
    TraceManager.__init__(manager)

    class _Q:
        def emit(self, event): pass

    manager._queue, manager._running = _Q(), True
    return manager


def _emit(manager, *events):
    for event in events:
        asyncio.run(manager.emit(event))
    return events


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
def test_the_manager_keeps_a_session_log_for_projection():
    manager = _manager()
    _emit(manager,
          agent_start_event("s", "t", "a", "task"),
          agent_call_event("s", "t", "a", 1, reasoning="looking"),
          tool_start_event("s", "t", "a", 1, 0, "bash_tool", {"command": "ls"}, call_id="c1"),
          tool_call_event("s", "t", "a", 1, 0, "bash_tool", "a.py", True, call_id="c1"))

    events = manager.events("s")
    assert [e.seq_no for e in events] == [0, 1, 2, 3]


def test_sessions_are_kept_apart():
    manager = _manager()
    _emit(manager, agent_start_event("a", "t", "x", "one"), agent_start_event("b", "t", "x", "two"))

    assert len(manager.events("a")) == 1
    assert len(manager.events("b")) == 1


def test_an_unknown_session_is_empty_not_an_error():
    assert _manager().events("never-seen") == []


def test_forget_releases_a_finished_session():
    manager = _manager()
    _emit(manager, agent_start_event("s", "t", "a", "task"))
    manager.forget("s")

    assert manager.events("s") == []
    assert manager.surface("s") == []


def test_overflow_drops_the_log_rather_than_keeping_a_suffix():
    """A truncated log projects as a shorter conversation, not a missing one.

    Keeping the tail would hand the model a history whose opening turns had vanished
    with nothing marking the gap — worse than admitting the log is not held.
    """
    manager = _manager()
    manager._max_retained = 3
    _emit(manager, *[agent_start_event("s", "t", "a", f"task {i}") for i in range(5)])
    assert manager.events("s") == []

    # And it stays dropped: resuming would rebuild a suffix that looks whole, which is
    # the same failure arriving a few thousand events later.
    _emit(manager, agent_start_event("s", "t", "a", "later"))
    assert manager.events("s") == []


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #
class _Agent:
    """The two methods under test, lifted off `Agent` to keep the fixture small."""

    from agentevolver.agent.types import Agent as _Base
    _derived_messages = _Base._derived_messages
    # staticmethod on Agent; re-wrap or the class body rebinds it as a method
    _split_rendered_turn = staticmethod(_Base._split_rendered_turn)
    _freeze_capabilities = staticmethod(_Base._freeze_capabilities)
    name = "probe_agent"


def _ctx(session_id="s"):
    from types import SimpleNamespace
    return SimpleNamespace(id=session_id, extra={})


_RENDERED = [SystemMessage(content="system"), HumanMessage(content="rendered transcript")]


def test_it_is_off_unless_asked_for():
    """A behavioural change to every agent is opt-in, per agent, against a measurement."""
    import inspect

    from agentevolver.agent.types import Agent

    assert inspect.signature(Agent.__init__).parameters["derive_context"].default is False


def test_the_projection_is_reached_only_when_the_flag_is_on():
    """The guard, read off the source: with the flag off nothing touches the log."""
    import inspect

    from agentevolver.agent.types import Agent

    body = inspect.getsource(Agent._get_messages)
    assert "if self.derive_context:" in body
    assert body.index("if self.derive_context:") < body.index("_derived_messages")


def test_a_session_with_no_retained_log_falls_back(monkeypatch):
    from agentevolver.trace import server as trace_server

    monkeypatch.setattr(trace_server.trace_manager, "_events", {}, raising=False)
    out = _Agent()._derived_messages(_RENDERED, _ctx("absent"))

    assert out == _RENDERED, "an unheld log must not read as an empty conversation"


def test_a_projectable_log_replaces_the_transcript(monkeypatch):
    from agentevolver.trace import server as trace_server

    manager = _manager()
    _emit(manager,
          agent_start_event("s2", "t", "a", "fix the bug"),
          agent_call_event("s2", "t", "a", 1, reasoning="looking"),
          tool_start_event("s2", "t", "a", 1, 0, "bash_tool", {"command": "ls"}, call_id="c1"),
          tool_call_event("s2", "t", "a", 1, 0, "bash_tool", "a.py", True, call_id="c1"))
    monkeypatch.setattr(trace_server.trace_manager, "_events", manager._events, raising=False)

    rendered = [SystemMessage(content="sys"),
                HumanMessage(content="<agent-context><task>T</task>"
                                     "<memory>rendered transcript</memory></agent-context>")]
    out = _Agent()._derived_messages(rendered, _ctx("s2"))

    assert isinstance(out[0], SystemMessage), "the system prompt is rendered, not logged"
    assert [type(m).__name__ for m in out[1:]] == [
        "HumanMessage", "AssistantMessage", "ToolMessage",
    ]
    assert "rendered transcript" not in " ".join(getattr(m, "text", "") for m in out)


def test_a_turn_with_no_recognisable_blocks_is_kept():
    """Content the stripper does not understand is carried, not discarded.

    A prompt template this code has never seen would otherwise lose whatever it says.
    """
    from agentevolver.agent.types import Agent

    _, volatile = Agent._split_rendered_turn(
        [SystemMessage(content="s"), HumanMessage(content="something unstructured")])
    assert volatile and "something unstructured" in volatile[0].text


def test_a_log_that_cannot_be_projected_falls_back(monkeypatch):
    """Refusing to project is the safe half; substituting a wrong history is not."""
    from agentevolver.trace import server as trace_server
    from agentevolver.trace.surface import replace_op

    manager = _manager()
    _emit(manager,
          agent_start_event("s3", "t", "a", "task"),
          agent_call_event("s3", "t", "a", 1, reasoning="x"))
    # A replacement that does not cite what it shadows: the fold refuses the log.
    broken = TraceEvent(event_type=TraceEventType.CUSTOM, session_id="s3",
                        surface_op=replace_op(0, 1), source_event_seqs=[0])
    asyncio.run(manager.emit(broken))
    monkeypatch.setattr(trace_server.trace_manager, "_events", manager._events, raising=False)

    assert _Agent()._derived_messages(_RENDERED, _ctx("s3")) == _RENDERED


# --------------------------------------------------------------------------- #
# The per-step scaffolding the projection must not drop
# --------------------------------------------------------------------------- #
_AGENT_TURN = (
    "<tool-context>[every tool, identical every step]</tool-context>"
    "<skill-context>[every skill, identical every step]</skill-context>"
    "<agent-context>"
    "<task>reverse a string</task>"
    "<constraints>steps 5/30</constraints>"
    "<step-info>you have measured enough</step-info>"
    "<memory>[history prose]</memory>"
    "<todo>- write the tests</todo>"
    "<workspace>/ws</workspace>"
    "<errors>- this exact call was issued 3 times</errors>"
    "</agent-context>"
)


def _rendered_with_scaffolding():
    return [SystemMessage(content="sys"), HumanMessage(content=_AGENT_TURN)]


def test_the_per_step_blocks_survive_the_projection():
    """Replacing the rendered turn wholesale silently switched these off.

    `errors` is where the repeat reminder rides, so dropping the turn meant turning
    `derive_context` on quietly disabled the loop guard — one feature bypassing another.
    """
    from agentevolver.agent.types import Agent

    _, volatile = Agent._split_rendered_turn(_rendered_with_scaffolding())

    for block in ("constraints", "step-info", "todo", "workspace", "errors"):
        assert f"<{block}>" in volatile[0].text, f"{block} was dropped"
    assert "this exact call was issued 3 times" in volatile[0].text, "repeat reminder lost"


def test_the_capability_catalogs_are_stable_not_volatile():
    """They are identical every step, and putting them after the history is what cut
    prefix reuse to 20% — 61,000 unchanging characters beyond the last reusable byte."""
    from agentevolver.agent.types import Agent

    stable, volatile = Agent._split_rendered_turn(_rendered_with_scaffolding())

    assert "<tool-context>" in stable[0].text and "<skill-context>" in stable[0].text
    assert "tool-context" not in volatile[0].text, "catalogs must not trail the history"


def test_history_blocks_are_not_stated_twice():
    """The projection opens with the task and *is* the history."""
    from agentevolver.agent.types import Agent

    stable, volatile = Agent._split_rendered_turn(_rendered_with_scaffolding())
    everything = (stable[0].text if stable else "") + (volatile[0].text if volatile else "")

    assert "<task>" not in everything
    assert "<memory>" not in everything
    assert "[history prose]" not in everything


def test_a_turn_carrying_only_history_adds_nothing():
    """A step with no scaffolding should append no turn, not an empty one."""
    from agentevolver.agent.types import Agent

    only_history = [SystemMessage(content="s"),
                    HumanMessage(content="<agent-context><task>T</task><memory>H</memory></agent-context>")]
    assert Agent._split_rendered_turn(only_history) == ([], [])


def test_the_scaffolding_lands_after_the_history(monkeypatch):
    """Volatile content belongs after the last stable byte, or the prefix never settles."""
    from agentevolver.trace import server as trace_server

    manager = _manager()
    _emit(manager,
          agent_start_event("s4", "t", "a", "reverse a string"),
          agent_call_event("s4", "t", "a", 1, reasoning="looking"),
          tool_start_event("s4", "t", "a", 1, 0, "bash_tool", {"command": "ls"}, call_id="c1"),
          tool_call_event("s4", "t", "a", 1, 0, "bash_tool", "a.py", True, call_id="c1"))
    monkeypatch.setattr(trace_server.trace_manager, "_events", manager._events, raising=False)

    out = _Agent()._derived_messages(_rendered_with_scaffolding(), _ctx("s4"))

    assert isinstance(out[0], SystemMessage)
    assert "<tool-context>" in out[1].text, "catalogs belong ahead of the history"
    assert "<constraints>" in out[-1].text, "per-step blocks belong after it"
    assert [type(m).__name__ for m in out[2:-1]] == [
        "HumanMessage", "AssistantMessage", "ToolMessage",
    ]


# --------------------------------------------------------------------------- #
# Evolution changes the catalog mid-session — the case this framework exists for
# --------------------------------------------------------------------------- #
def _catalog(*skills):
    body = "\n".join(f"- {s}" for s in skills)
    return [HumanMessage(content=f"<skill-context>\n{body}\n</skill-context>")]


def test_the_catalog_is_frozen_after_the_first_render():
    """A rebuilt catalog is not an appended one.

    Measured on a real registry, removing one skill of eighty-four leaves a common
    prefix of four characters — the list is rewritten, not extended. At the front of the
    request that invalidates the whole conversation behind it.
    """
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    first = _catalog("alpha: A", "beta: B")
    Agent._freeze_capabilities(first, ctx)

    grown = _catalog("alpha: A", "beta: B", "gamma: generated mid-run")
    frozen, _ = Agent._freeze_capabilities(grown, ctx)

    assert frozen[0].text == first[0].text, "the cached prefix was rewritten"


def test_a_new_capability_is_announced_after_the_catalog():
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    Agent._freeze_capabilities(_catalog("alpha: A"), ctx)
    _, addition = Agent._freeze_capabilities(_catalog("alpha: A", "gamma: new"), ctx)

    assert addition, "a capability the model may now use must be stated"
    assert "gamma: new" in addition[0].text
    assert "now available" in addition[0].text


def test_a_withdrawn_capability_is_announced_too():
    """Evolution replaces components as well as adding them."""
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    Agent._freeze_capabilities(_catalog("alpha: A", "beta: B"), ctx)
    _, addition = Agent._freeze_capabilities(_catalog("alpha: A"), ctx)

    assert addition and "no longer available" in addition[0].text
    assert "beta: B" in addition[0].text


def test_an_unchanged_catalog_says_nothing():
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    same = _catalog("alpha: A")
    Agent._freeze_capabilities(same, ctx)
    frozen, addition = Agent._freeze_capabilities(same, ctx)

    assert addition == []
    assert frozen[0].text == same[0].text


def test_the_prefix_survives_an_evolution_step():
    """The property the whole mechanism is for, asserted end to end."""
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    before, _ = Agent._freeze_capabilities(_catalog("alpha: A", "beta: B"), ctx)
    after, addition = Agent._freeze_capabilities(
        _catalog("alpha: A", "beta: B", "gamma: new"), ctx)

    a, b = before[0].text, after[0].text
    common = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    assert common == len(a), "the frozen catalog must stay byte-identical"
    assert addition, "and the change must still reach the model"
