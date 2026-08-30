"""The derived-history switch, and what it does when the log cannot support it.

`derive_context` changes what every step sees and is now the default. These tests pin
both paths: disabling it is exactly the old path, and projection never silently hands
the model a shorter conversation than the one that happened.
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


def test_conversation_projection_is_the_default():
    """Agents see native assistant/tool history unless a compatibility run opts out."""
    import inspect

    from agentevolver.agent.types import Agent

    assert inspect.signature(Agent.__init__).parameters["derive_context"].default is True


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
    assert "<task>" in out[1].text, "the stable task belongs ahead of the history"
    assert all("<tool-context>" not in message.text for message in out), (
        "native tool definitions replace the duplicated prose catalog"
    )
    assert "<constraints>" in out[-1].text, "per-step blocks belong after it"
    assert "<task>\nreverse a string\n</task>" in out[1].text
    assert [type(m).__name__ for m in out[2:-1]] == [
        "AssistantMessage", "ToolMessage",
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


def test_a_change_is_announced_in_the_block_it_belongs_to():
    """A generated skill is a skill. It is not a new kind of thing.

    Announcing every change in one invented block made the model merge a catalog with
    a change log to answer "what skills do I have" — a vocabulary the prompt never
    defined. The block type repeats instead.
    """
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    before = [HumanMessage(content="<tool-context>\n- bash: run\n</tool-context>\n"
                                   "<skill-context>\n- alpha: A\n</skill-context>")]
    after = [HumanMessage(content="<tool-context>\n- bash: run\n- csv: new tool\n</tool-context>\n"
                                  "<skill-context>\n- alpha: A\n- gamma: new skill\n</skill-context>")]
    Agent._freeze_capabilities(before, ctx)
    _, addition = Agent._freeze_capabilities(after, ctx)

    text = addition[0].text
    assert "<capability-changes>" not in text, "no invented block type"
    tools = text[text.index("<tool-context>"):text.index("</tool-context>")]
    skills = text[text.index("<skill-context>"):text.index("</skill-context>")]
    assert "csv: new tool" in tools and "gamma" not in tools
    assert "gamma: new skill" in skills and "csv" not in skills


def test_an_untouched_block_is_not_mentioned():
    """Only what changed is restated; the rest is already above, byte for byte."""
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    before = [HumanMessage(content="<tool-context>\n- bash: run\n</tool-context>\n"
                                   "<skill-context>\n- alpha: A\n</skill-context>")]
    after = [HumanMessage(content="<tool-context>\n- bash: run\n</tool-context>\n"
                                  "<skill-context>\n- alpha: A\n- gamma: new\n</skill-context>")]
    Agent._freeze_capabilities(before, ctx)
    _, addition = Agent._freeze_capabilities(after, ctx)

    assert "<tool-context>" not in addition[0].text
    assert "<skill-context>" in addition[0].text


def test_a_withdrawn_capability_is_announced_too():
    """Evolution replaces components as well as adding them."""
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    Agent._freeze_capabilities(_catalog("alpha: A", "beta: B"), ctx)
    _, addition = Agent._freeze_capabilities(_catalog("alpha: A"), ctx)

    assert addition and "no longer available" in addition[0].text
    assert "beta: B" in addition[0].text
    assert "do not call" in addition[0].text, "the model must be told not to try it"


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


# --------------------------------------------------------------------------- #
# The container: one catalog, one cache breakpoint
# --------------------------------------------------------------------------- #
def test_the_container_is_treated_as_stable():
    """`<capability-context>` is the catalog now, and the split must follow the template.

    The predecessor listed the four leaf tags by hand. That list and the templates were
    two records of the same fact, and only one of them was edited when the blocks were
    merged — the split then found nothing stable and sent the whole 58,000-character
    catalog after the history, where no cache can reach it.
    """
    from agentevolver.agent.types import Agent

    turn = [HumanMessage(content=(
        "<capability-context>\n<tool-context>- bash</tool-context>\n"
        "<skill-context>- alpha</skill-context>\n</capability-context>\n"
        "<agent-context><step-info>step 3</step-info></agent-context>"))]
    stable, volatile = Agent._split_rendered_turn(turn)

    assert "<capability-context>" in stable[0].text
    assert "bash" in stable[0].text and "alpha" in stable[0].text
    assert "capability-context" not in volatile[0].text
    assert "step 3" in volatile[0].text


def test_bare_blocks_still_split_without_the_container():
    """A prompt written before the container existed must not lose its catalog."""
    from agentevolver.agent.types import Agent

    turn = [HumanMessage(content="<tool-context>- bash</tool-context>\n"
                                 "<agent-context><step-info>step 3</step-info></agent-context>")]
    stable, volatile = Agent._split_rendered_turn(turn)
    assert "bash" in stable[0].text
    assert "step 3" in volatile[0].text


def test_changes_are_wrapped_in_a_container_that_mirrors_the_catalog():
    """The change block names a container the prompt actually defines.

    While the catalogs were four loose blocks there was nothing for a change block to
    refer to, so any name for it introduced a concept of its own. With one container the
    update can say which catalog it updates — and the leaves inside keep their own tags,
    so a new skill is still announced as a skill.
    """
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    before = [HumanMessage(content="<capability-context>\n<tool-context>\n- bash: run\n"
                                   "</tool-context>\n<skill-context>\n- alpha: A\n"
                                   "</skill-context>\n</capability-context>")]
    after = [HumanMessage(content="<capability-context>\n<tool-context>\n- bash: run\n"
                                  "</tool-context>\n<skill-context>\n- alpha: A\n"
                                  "- gamma: new skill\n</skill-context>\n</capability-context>")]
    Agent._freeze_capabilities(before, ctx)
    frozen, addition = Agent._freeze_capabilities(after, ctx)

    text = addition[0].text
    assert text.startswith("<capability-context-changes>")
    assert "<skill-context>" in text and "gamma: new skill" in text
    assert "<tool-context>" not in text, "an untouched leaf is not restated"
    # The container wraps the leaves; it is not diffed as a leaf itself, which would
    # report every change a second time as one undifferentiated lump.
    assert text.count("gamma: new skill") == 1
    assert frozen[0].text == before[0].text, "the frozen catalog goes out byte-identical"


def test_the_catalog_is_re_taken_once_the_delta_grows_too_large():
    """Freezing is not free forever.

    Each change lengthens the announcement while the frozen catalog grows staler. Left
    alone, a long evolving session carries a change log rivalling the catalog it patches
    — paying for both and asking the model to reconcile them every step. Past the ratio
    *and* past the absolute floor, one cache write buys a prompt that states its
    capabilities once.
    """
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    base = ("<capability-context>\n<skill-context>\n"
            + "".join(f"- skill_{i:02d}: does a thing in the workspace\n" for i in range(30))
            + "</skill-context>\n</capability-context>")
    Agent._freeze_capabilities([HumanMessage(content=base)], ctx)

    grown = base.replace("</skill-context>", "".join(
        f"- generated_{i:03d}: written mid-run by the evolver\n" for i in range(90))
        + "</skill-context>")
    frozen, addition = Agent._freeze_capabilities([HumanMessage(content=grown)], ctx)

    assert addition == [], "past both thresholds the delta is dropped, not carried"
    assert frozen[0].text == grown, "the catalog sent is the current one"
    assert ctx.extra["_capability_snapshot"] == grown, "and it becomes the new baseline"


def test_a_large_ratio_alone_does_not_re_take_a_small_catalog():
    """The floor. Re-freezing costs a cache write of the whole catalog.

    Against a two-line catalog the ratio fires on the first change — the per-line
    prefixes outweigh the catalog itself — and paying for a re-write to retire a few
    hundred characters is never the trade.
    """
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    base = "<capability-context>\n<skill-context>\n- alpha: A\n</skill-context>\n</capability-context>"
    Agent._freeze_capabilities([HumanMessage(content=base)], ctx)

    grown = base.replace("- alpha: A\n", "- alpha: A\n- gamma: generated mid-run\n")
    frozen, addition = Agent._freeze_capabilities([HumanMessage(content=grown)], ctx)

    assert "gamma: generated mid-run" in addition[0].text
    assert frozen[0].text == base


def test_a_small_delta_is_still_carried_rather_than_re_taken():
    """The common case. Re-taking on every change would defeat the whole mechanism."""
    from agentevolver.agent.types import Agent

    ctx = _ctx()
    base = ("<capability-context>\n<skill-context>\n"
            + "".join(f"- skill_{i:02d}: does a thing\n" for i in range(40))
            + "</skill-context>\n</capability-context>")
    Agent._freeze_capabilities([HumanMessage(content=base)], ctx)

    grown = base.replace("</skill-context>", "- gamma: generated mid-run\n</skill-context>")
    frozen, addition = Agent._freeze_capabilities([HumanMessage(content=grown)], ctx)

    assert "gamma: generated mid-run" in addition[0].text
    assert frozen[0].text == base, "the frozen prefix is held still"
    assert ctx.extra["_capability_snapshot"] == base


# --------------------------------------------------------------------------- #
# Freezing is not only for the projection
# --------------------------------------------------------------------------- #
def _rendered_turn(skills: str):
    from agentevolver.message.types import SystemMessage
    return [SystemMessage(content="rules"),
            HumanMessage(content=(
                "<capability-context>\n<tool-context>\n- bash: run\n</tool-context>\n"
                f"<skill-context>\n{skills}\n</skill-context>\n</capability-context>\n"
                "<agent-context><step-info>step N</step-info></agent-context>"))]


def _agent():
    from agentevolver.agent.types import Agent
    return Agent.__new__(Agent)          # the method under test needs no construction


def test_the_default_path_freezes_the_catalog_too():
    """The switch nobody has turned on was the only path protected.

    The default path re-rendered the catalog live every step, so the first component this
    framework generated rewrote it — and the catalog sits ahead of the cache breakpoint,
    so rewriting it invalidates the prefix for the rest of the session. Self-evolution
    would have cancelled the caching it had just gained.
    """
    from agentevolver.agent.types import Agent

    ctx, agent = _ctx(), _agent()
    Agent._frozen_rendered(agent, _rendered_turn("- alpha: A"), ctx)
    out = Agent._frozen_rendered(
        agent, _rendered_turn("- alpha: A\n- gamma: generated mid-run"), ctx)

    catalog = out[1].text[:out[1].text.index("</capability-context>")]
    assert "gamma" not in catalog, "the frozen catalog must go out byte-identical"
    assert "gamma: generated mid-run" in out[1].text, "but the change must still be stated"


def test_the_delta_lands_after_the_breakpoint():
    """Appended to the end of the same turn, not spliced into the catalog.

    The breakpoint is `</capability-context>`; anything before it must not move, and
    anything after it is outside the cached prefix and free to change.
    """
    from agentevolver.agent.types import Agent

    ctx, agent = _ctx(), _agent()
    first = Agent._frozen_rendered(agent, _rendered_turn("- alpha: A"), ctx)
    second = Agent._frozen_rendered(
        agent, _rendered_turn("- alpha: A\n- gamma: new"), ctx)

    marker = "</capability-context>"
    assert (second[1].text[:second[1].text.index(marker)]
            == first[1].text[:first[1].text.index(marker)]), \
        "every byte up to the breakpoint must be unchanged"
    assert second[1].text.rstrip().endswith("</capability-context-changes>")


def test_an_unchanged_catalog_is_left_exactly_alone():
    """The common case: no rebuild, so no chance of differing by a stray newline."""
    from agentevolver.agent.types import Agent

    ctx, agent = _ctx(), _agent()
    Agent._frozen_rendered(agent, _rendered_turn("- alpha: A"), ctx)
    same = _rendered_turn("- alpha: A")
    assert Agent._frozen_rendered(agent, same, ctx) is same


def test_a_turn_without_a_catalog_passes_through():
    """Not every agent loads capabilities; those turns must not be rewritten."""
    from agentevolver.agent.types import Agent
    from agentevolver.message.types import SystemMessage

    ctx, agent = _ctx(), _agent()
    plain = [SystemMessage(content="rules"), HumanMessage(content="<agent-context/>")]
    assert Agent._frozen_rendered(agent, plain, ctx) is plain
