"""What a sub-agent inherits from its parent, and what it must never inherit.

The runtime moves messages; this layer decides the shape of each conversation. The rule
that costs the most when broken is ambient inheritance on delegation. A child runs in the
same place as its parent, so the roots have to carry across — a child told different paths
than its parent goes looking for files where they are not. That was seen on ProgramBench:
the sub-agent ran ``find /`` over a whole filesystem hunting for task files that existed
elsewhere, and nothing in the run said the context had failed to carry. The mirror rule is
just as expensive: per-delegation state (the target, the allowlists, the lineage ids) must
*not* carry across, or a child silently inherits whatever scope its sibling was given.

Escalation's promise is that a blocked sub-agent always gets an answer. No parent, a
parent that has already finished, and a parent that never replies each return usable
guidance, because the alternative is an agent suspended forever on a reply that is not
coming.
"""

import pytest

from agentevolver.protocol.server import _AMBIENT_CONTEXT_KEYS, ProtocolManager, _inherited_ambient
from agentevolver.protocol.types import (
    ControlMessage,
    EscalationMessage,
    MonitorProgressMessage,
    QueryMessage,
)


class Ctx:
    """A stand-in for an AgentContext, with lineage on the field."""

    def __init__(self, id="child", name="code_agent", extra=None, **kw):
        self.id = id
        self.name = name
        self.extra = extra or {}
        self.parent_session_id = kw.get("parent_session_id")
        self.subtask_id = kw.get("subtask_id")


@pytest.fixture
def protocol():
    """A manager built without ``__init__``.

    ``ProtocolManager`` is a singleton, so constructing it normally would hand back the
    process-wide instance and leak state between tests.
    """
    return ProtocolManager.__new__(ProtocolManager)


@pytest.fixture
def runtime(monkeypatch):
    """A recording stand-in for ``runtime_manager``.

    Every transport verb is captured rather than performed, so the assertions are about
    which message was sent to whom — the part this layer decides — with no event loop,
    no live agents and no real suspension involved.
    """

    class FakeRuntime:
        def __init__(self):
            self.sent = []
            self.published = []
            self.subscriptions = []
            self.refs = {}
            self.suspend_result = "keep going"
            self.suspend_error = None
            self.resume_result = True
            self.ask_result = {"step": 3}

        def get(self, session_id):
            return self.refs.get(session_id)

        async def send(self, ref, msg):
            self.sent.append((ref, msg))

        async def suspend(self, task_id, timeout=None):
            if self.suspend_error:
                raise self.suspend_error
            return self.suspend_result

        def resume(self, task_id, guidance):
            self.sent.append(("resume", task_id, guidance))
            return self.resume_result

        async def ask(self, ref, msg, timeout=None):
            return self.ask_result

        def subscribe(self, topic, ref):
            self.subscriptions.append(("sub", topic, ref))

        def unsubscribe(self, topic, ref):
            self.subscriptions.append(("unsub", topic, ref))

        async def publish(self, topic, msg):
            self.published.append((topic, msg))
            return 2

    fake = FakeRuntime()
    monkeypatch.setattr("agentevolver.protocol.server.runtime_manager", fake)
    return fake


# --------------------------------------------------------------------------- #
# What crosses a delegation boundary
# --------------------------------------------------------------------------- #
def test_the_execution_environment_carries_into_a_sub_agent():
    """Every ambient key the parent holds reaches the child, not a subset.

    The list is read from the module, so a key added later is covered without editing
    this test — which is the point: the failure mode is a key being introduced and never
    wired into the delegation path.
    """
    parent = Ctx(extra={key: f"/{key}" for key in _AMBIENT_CONTEXT_KEYS})
    assert _inherited_ambient(parent) == {key: f"/{key}" for key in _AMBIENT_CONTEXT_KEYS}


def test_the_roots_are_among_what_is_inherited():
    """A child told different paths than its parent goes looking for files where
    they are not.

    Named explicitly because these three are the ones whose absence produced the
    ProgramBench failure, and because "the child works, just somewhere else" is not a
    symptom anybody traces back to a context key.
    """
    assert {"workspace_root", "log_root", "project_root"} <= set(_AMBIENT_CONTEXT_KEYS)


@pytest.mark.parametrize("key", ["target_name", "parent_session_id", "subtask_id", "tool_names"])
def test_per_delegation_state_does_not_leak_into_a_sibling(key):
    """These are scoped to one delegation; inheriting them would widen a child's
    scope to whatever the last one had.

    They sit in the same ``extra`` dict as the ambient keys, so adding one to the
    inherited list is a one-word mistake — and its effect is a sub-agent quietly holding
    a tool allowlist, or a lineage id, that was granted to a different delegation.
    """
    assert key not in _AMBIENT_CONTEXT_KEYS


def test_a_parent_with_no_context_yields_nothing_rather_than_failing():
    """Top-level agents have no parent at all, and a context may carry no ``extra``.
    Delegation runs through this on every call, including the very first one."""
    assert _inherited_ambient(None) == {}
    assert _inherited_ambient(Ctx(extra=None)) == {}


def test_only_keys_the_parent_actually_has_are_inherited():
    """Absent keys stay absent rather than arriving as ``None``.

    A child whose ``workspace_root`` is present-but-empty resolves paths against nothing,
    which is harder to spot than the key simply not being there.
    """
    assert _inherited_ambient(Ctx(extra={"workspace_root": "/ws"})) == {"workspace_root": "/ws"}


# --------------------------------------------------------------------------- #
# A blocked sub-agent always gets an answer
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_standalone_agent_is_told_to_proceed_rather_than_left_hanging(protocol, runtime):
    """An agent run directly has nobody to ask, and may still call ``escalate``.

    Nothing is sent — asserting on that, and not only on the text, is what separates
    "there was no parent" from "the message went somewhere and the reply was a default".
    """
    guidance = await protocol.escalate(Ctx(), reason="stuck")
    assert "No parent" in guidance
    assert runtime.sent == []


@pytest.mark.asyncio
async def test_a_parent_that_has_already_finished_is_reported_as_such(protocol, runtime):
    """A lineage id outlives the agent it names. Suspending on a parent that has already
    returned would block until the escalation timeout for a reply nobody can send."""
    guidance = await protocol.escalate(Ctx(parent_session_id="gone"), reason="stuck")
    assert "no longer running" in guidance


@pytest.mark.asyncio
async def test_the_guidance_comes_back_to_the_blocked_agent(protocol, runtime):
    """The happy path, end to end: the parent's answer is returned verbatim.

    ``task_id`` is checked alongside it because that is the key the reply is routed on —
    a suspension registered under one id and resumed under another leaves the child
    waiting while the parent believes it has answered.
    """
    runtime.refs["parent-1"] = object()
    runtime.suspend_result = "try the other approach"
    guidance = await protocol.escalate(
        Ctx(parent_session_id="parent-1", subtask_id="sub-1"),
        reason="stuck",
        situation="tests fail",
        suggestion="rebuild?",
    )
    assert guidance == "try the other approach"

    _, message = runtime.sent[0]
    assert isinstance(message, EscalationMessage)
    assert message.task_id == "sub-1"
    assert message.reason == "stuck"


@pytest.mark.asyncio
async def test_a_silent_parent_still_unblocks_the_child(protocol, runtime):
    """Waiting forever on a parent that never answers would strand the sub-agent.

    A parent busy in its own model call can miss the escalation entirely. The timeout is
    turned into an instruction the child can act on, rather than an exception thrown
    inside a tool it called for help.
    """
    runtime.refs["parent-1"] = object()
    runtime.suspend_error = TimeoutError("no reply")
    guidance = await protocol.escalate(Ctx(parent_session_id="parent-1"), reason="stuck")
    assert "stop the current subtask gracefully" in guidance


@pytest.mark.asyncio
async def test_lineage_is_found_in_extra_after_a_context_conversion(protocol, runtime):
    """A tool's ToolContext carries lineage in ``extra``, not on a field.

    Escalation is almost always reached from inside a tool, so the converted shape is the
    normal one. Reading only the attribute would make every escalation report "no parent"
    on exactly the path that matters.
    """
    runtime.refs["parent-1"] = object()
    ctx = Ctx(
        parent_session_id=None, extra={"parent_session_id": "parent-1", "subtask_id": "sub-9"}
    )
    await protocol.escalate(ctx, reason="stuck")
    assert runtime.sent[0][1].task_id == "sub-9"


@pytest.mark.asyncio
async def test_an_escalation_without_a_subtask_id_falls_back_to_the_session(protocol, runtime):
    """An agent invoked without a subtask still has an id worth suspending on; an empty
    ``task_id`` would make the reply unroutable."""
    runtime.refs["parent-1"] = object()
    await protocol.escalate(Ctx(id="ctx-7", parent_session_id="parent-1"), reason="stuck")
    assert runtime.sent[0][1].task_id == "ctx-7"


def test_replying_reports_whether_anyone_was_waiting(protocol, runtime):
    """The parent needs to know its answer landed.

    A reply to a subtask that already timed out is silently discarded otherwise, and the
    parent goes on believing it unblocked a child that is meanwhile winding itself down.
    """
    assert protocol.reply("sub-1", "do this") is True
    runtime.resume_result = False
    assert protocol.reply("sub-1", "do this") is False


# --------------------------------------------------------------------------- #
# Steering a running agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verb, action", [("cancel", "cancel"), ("pause", "pause"), ("resume", "resume")]
)
async def test_each_control_verb_sends_its_instruction(protocol, runtime, verb, action):
    """Three near-identical one-line methods, which is exactly how one ends up sending
    another's action — a ``pause`` that cancels is unrecoverable, and the caller sees only
    that the agent stopped."""
    ref = object()
    await getattr(protocol, verb)(ref)
    sent_ref, message = runtime.sent[0]
    assert sent_ref is ref
    assert isinstance(message, ControlMessage)
    assert message.action == action


@pytest.mark.asyncio
async def test_a_cancellation_can_say_why(protocol, runtime):
    """The reason is what the cancelled agent reports as its outcome; without it a run
    killed for budget is indistinguishable from one killed by a user."""
    await protocol.cancel(object(), reason="budget exhausted")
    assert runtime.sent[0][1].reason == "budget exhausted"


# --------------------------------------------------------------------------- #
# Progress and status
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_progress_reaches_the_parent(protocol, runtime):
    ref = object()
    await protocol.report(ref, MonitorProgressMessage(task_id="t1", status="running"))
    assert runtime.sent[0][0] is ref


@pytest.mark.asyncio
async def test_progress_with_no_parent_is_silently_dropped(protocol, runtime):
    """Fire-and-forget: a standalone run must not fail because nobody is listening.

    The same agent code runs delegated and standalone, and reporting is on the hot path —
    raising here would break every top-level run at its first status update.
    """
    await protocol.report(None, MonitorProgressMessage(task_id="t1"))
    assert runtime.sent == []


@pytest.mark.asyncio
async def test_a_query_returns_the_agent_s_snapshot(protocol, runtime):
    """Unlike progress, a query is a round trip — the caller waits for the answer and
    gets the agent's own reply back rather than an acknowledgement."""
    assert await protocol.query(object()) == {"step": 3}


# --------------------------------------------------------------------------- #
# Broadcast
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_publishing_reports_the_fan_out_count(protocol, runtime):
    """The count is the only feedback a publisher gets. Zero means nobody was subscribed,
    which is how a topic name typo shows itself — and it looks identical to a successful
    publish if the number is thrown away."""
    assert await protocol.publish("evolution", {"event": "x"}) == 2


def test_subscription_is_passed_straight_through(protocol, runtime):
    """Subscribe and unsubscribe add no logic of their own, so what is worth pinning is
    that they reach the runtime as a matched pair on the same topic and ref — an
    unsubscribe that misses leaves an ended agent receiving events forever."""
    ref = object()
    protocol.subscribe("evolution", ref)
    protocol.unsubscribe("evolution", ref)
    assert runtime.subscriptions == [("sub", "evolution", ref), ("unsub", "evolution", ref)]


# --------------------------------------------------------------------------- #
# The message envelopes themselves
# --------------------------------------------------------------------------- #
def test_an_escalation_renders_without_an_empty_suggestion_line():
    """The rendered text goes into the parent's prompt.

    A dangling "Suggestion:" with nothing after it reads as a suggestion the child made
    and the parent failed to receive, which is worse than the line being absent.
    """
    without = EscalationMessage(task_id="t", reason="stuck", situation="tests fail")
    assert "Suggestion" not in without.text
    with_it = EscalationMessage(task_id="t", reason="stuck", suggestion="rebuild")
    assert "Suggestion: rebuild" in with_it.text


def test_a_progress_update_starts_as_running_with_no_exit_code():
    """``exit_code`` is how a watcher tells a finished task from a live one, so it has to
    stay ``None`` until there is one — a default of 0 reports success on the first update
    of every subprocess."""
    message = MonitorProgressMessage(task_id="t")
    assert message.status == "running"
    assert message.exit_code is None


def test_an_unknown_control_action_is_rejected():
    """The action is a string on the wire and is dispatched on by name; an unrecognised
    one would otherwise be delivered and quietly ignored by the receiving agent."""
    with pytest.raises(Exception):
        ControlMessage(action="explode")


def test_an_empty_query_asks_for_everything():
    """``None`` has to mean the full snapshot rather than no fields, or the default query
    — the one every caller makes — comes back empty."""
    assert QueryMessage().fields is None
