"""Protocol: the shape of each agent-to-agent conversation.

The runtime moves messages; this layer decides what each conversation looks
like. The rule that costs the most when broken is ambient inheritance on
delegation: a child that does not inherit its parent's roots is told different
paths than its parent while working in the same place. That was seen on
ProgramBench — the sub-agent ran ``find /`` over a whole filesystem hunting for
task files that existed elsewhere. Per-delegation state (the target, allowlists,
lineage ids) must *not* carry across, or a child inherits a sibling's scope.

Escalation's other promise is that a blocked sub-agent always gets an answer:
no parent, a dead parent, and a timeout each return usable guidance instead of
leaving the child hanging.
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
    return ProtocolManager.__new__(ProtocolManager)


@pytest.fixture
def runtime(monkeypatch):
    """A recording stand-in for ``runtime_manager``."""

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


# ------------------------------------------------------- ambient inheritance
def test_the_execution_environment_carries_into_a_sub_agent():
    parent = Ctx(extra={key: f"/{key}" for key in _AMBIENT_CONTEXT_KEYS})
    assert _inherited_ambient(parent) == {key: f"/{key}" for key in _AMBIENT_CONTEXT_KEYS}


def test_the_roots_are_among_what_is_inherited():
    """A child told different paths than its parent goes looking for files where
    they are not."""
    assert {"workspace_root", "log_root", "project_root"} <= set(_AMBIENT_CONTEXT_KEYS)


@pytest.mark.parametrize("key", ["target_name", "parent_session_id", "subtask_id", "tool_names"])
def test_per_delegation_state_does_not_leak_into_a_sibling(key):
    """These are scoped to one delegation; inheriting them would widen a child's
    scope to whatever the last one had."""
    assert key not in _AMBIENT_CONTEXT_KEYS


def test_a_parent_with_no_context_yields_nothing_rather_than_failing():
    assert _inherited_ambient(None) == {}
    assert _inherited_ambient(Ctx(extra=None)) == {}


def test_only_keys_the_parent_actually_has_are_inherited():
    assert _inherited_ambient(Ctx(extra={"workspace_root": "/ws"})) == {"workspace_root": "/ws"}


# ------------------------------------------------------------- escalation
@pytest.mark.asyncio
async def test_a_standalone_agent_is_told_to_proceed_rather_than_left_hanging(protocol, runtime):
    guidance = await protocol.escalate(Ctx(), reason="stuck")
    assert "No parent" in guidance
    assert runtime.sent == []


@pytest.mark.asyncio
async def test_a_parent_that_has_already_finished_is_reported_as_such(protocol, runtime):
    guidance = await protocol.escalate(Ctx(parent_session_id="gone"), reason="stuck")
    assert "no longer running" in guidance


@pytest.mark.asyncio
async def test_the_guidance_comes_back_to_the_blocked_agent(protocol, runtime):
    runtime.refs["parent-1"] = object()
    runtime.suspend_result = "try the other approach"
    guidance = await protocol.escalate(
        Ctx(parent_session_id="parent-1", subtask_id="sub-1"),
        reason="stuck", situation="tests fail", suggestion="rebuild?",
    )
    assert guidance == "try the other approach"

    _, message = runtime.sent[0]
    assert isinstance(message, EscalationMessage)
    assert message.task_id == "sub-1"
    assert message.reason == "stuck"


@pytest.mark.asyncio
async def test_a_silent_parent_still_unblocks_the_child(protocol, runtime):
    """Waiting forever on a parent that never answers would strand the sub-agent."""
    runtime.refs["parent-1"] = object()
    runtime.suspend_error = TimeoutError("no reply")
    guidance = await protocol.escalate(Ctx(parent_session_id="parent-1"), reason="stuck")
    assert "stop the current subtask gracefully" in guidance


@pytest.mark.asyncio
async def test_lineage_is_found_in_extra_after_a_context_conversion(protocol, runtime):
    """A tool's ToolContext carries lineage in ``extra``, not on a field."""
    runtime.refs["parent-1"] = object()
    ctx = Ctx(parent_session_id=None, extra={"parent_session_id": "parent-1", "subtask_id": "sub-9"})
    await protocol.escalate(ctx, reason="stuck")
    assert runtime.sent[0][1].task_id == "sub-9"


@pytest.mark.asyncio
async def test_an_escalation_without_a_subtask_id_falls_back_to_the_session(protocol, runtime):
    runtime.refs["parent-1"] = object()
    await protocol.escalate(Ctx(id="ctx-7", parent_session_id="parent-1"), reason="stuck")
    assert runtime.sent[0][1].task_id == "ctx-7"


def test_replying_reports_whether_anyone_was_waiting(protocol, runtime):
    assert protocol.reply("sub-1", "do this") is True
    runtime.resume_result = False
    assert protocol.reply("sub-1", "do this") is False


# ----------------------------------------------------------------- control
@pytest.mark.asyncio
@pytest.mark.parametrize("verb, action", [("cancel", "cancel"), ("pause", "pause"), ("resume", "resume")])
async def test_each_control_verb_sends_its_instruction(protocol, runtime, verb, action):
    ref = object()
    await getattr(protocol, verb)(ref)
    sent_ref, message = runtime.sent[0]
    assert sent_ref is ref
    assert isinstance(message, ControlMessage)
    assert message.action == action


@pytest.mark.asyncio
async def test_a_cancellation_can_say_why(protocol, runtime):
    await protocol.cancel(object(), reason="budget exhausted")
    assert runtime.sent[0][1].reason == "budget exhausted"


# ---------------------------------------------------------------- progress
@pytest.mark.asyncio
async def test_progress_reaches_the_parent(protocol, runtime):
    ref = object()
    await protocol.report(ref, MonitorProgressMessage(task_id="t1", status="running"))
    assert runtime.sent[0][0] is ref


@pytest.mark.asyncio
async def test_progress_with_no_parent_is_silently_dropped(protocol, runtime):
    """Fire-and-forget: a standalone run must not fail because nobody is listening."""
    await protocol.report(None, MonitorProgressMessage(task_id="t1"))
    assert runtime.sent == []


# ------------------------------------------------------------------- query
@pytest.mark.asyncio
async def test_a_query_returns_the_agent_s_snapshot(protocol, runtime):
    assert await protocol.query(object()) == {"step": 3}


# ------------------------------------------------------------------ pubsub
@pytest.mark.asyncio
async def test_publishing_reports_the_fan_out_count(protocol, runtime):
    assert await protocol.publish("evolution", {"event": "x"}) == 2


def test_subscription_is_passed_straight_through(protocol, runtime):
    ref = object()
    protocol.subscribe("evolution", ref)
    protocol.unsubscribe("evolution", ref)
    assert runtime.subscriptions == [("sub", "evolution", ref), ("unsub", "evolution", ref)]


# ---------------------------------------------------------------- messages
def test_an_escalation_renders_without_an_empty_suggestion_line():
    without = EscalationMessage(task_id="t", reason="stuck", situation="tests fail")
    assert "Suggestion" not in without.text
    with_it = EscalationMessage(task_id="t", reason="stuck", suggestion="rebuild")
    assert "Suggestion: rebuild" in with_it.text


def test_a_progress_update_starts_as_running_with_no_exit_code():
    message = MonitorProgressMessage(task_id="t")
    assert message.status == "running"
    assert message.exit_code is None


def test_an_unknown_control_action_is_rejected():
    with pytest.raises(Exception):
        ControlMessage(action="explode")


def test_an_empty_query_asks_for_everything():
    assert QueryMessage().fields is None
