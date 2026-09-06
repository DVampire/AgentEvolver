"""Three things a rebuild loses quietly, and the tests that stop that happening again.

Each of these went missing once already, and none of them failed loudly when it did: a
program simply had nothing to call, a child simply started ignorant of what its parent
had established, and a newly generated capability simply sat unused. Silence is the
whole reason they are pinned here.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agentevolver.agent.loop import ActionCall, ActionResult, Agent, Decision, ToolRouter
from agentevolver.agent.loop.agent import INHERITED_CONTEXT_MAX
from agentevolver.agent.loop.executor import ActionExecutor
from agentevolver.agent.loop.guards import CapabilityChanges
from agentevolver.agent.loop.router import CapabilityRouter
from agentevolver.code import BATCH_CALL_TOOL
from agentevolver.message.types import AssistantMessage, Function, ToolCall, ToolMessage


class RecordingRouter(ToolRouter):
    """Remembers what it was asked to run, and whether it was handed a bridge."""

    def __init__(self, names=("read_file_tool", BATCH_CALL_TOOL)):
        self.names = list(names)
        self.seen: List[str] = []
        self.bridges: Dict[str, Any] = {}

    async def schemas(self, agent, ctx):
        return [], {name: ("tool", name) for name in self.names}

    def read_only(self, call, routing):
        return None

    async def invoke(self, call, *, agent, ctx, routing, execution=None, bridge=None):
        self.seen.append(call.name)
        self.bridges[call.name] = bridge
        return ActionResult(call=call, output=f"{call.name} ran")


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_program_transport_is_handed_a_dispatcher():
    """Every other call gets None: a dispatcher is a capability, not a convenience."""
    router = RecordingRouter()
    executor = ActionExecutor(router)
    agent = Agent(name="probe")
    calls = [
        ActionCall(id="a", name="read_file_tool", args={"path": "x"}),
        ActionCall(id="b", name=BATCH_CALL_TOOL, args={"program": "..."}),
    ]
    _, routing = await router.schemas(agent, None)
    await executor.run(calls, agent=agent, ctx=None, routing=routing)

    assert router.bridges["read_file_tool"] is None
    assert router.bridges[BATCH_CALL_TOOL] is not None


@pytest.mark.asyncio
async def test_a_program_reaches_a_tool_only_through_the_executor():
    """The safety argument for running a program: one dispatch path, one set of gates."""
    router = RecordingRouter()
    executor = ActionExecutor(router)
    agent = Agent(name="probe")
    call = ActionCall(id="prog", name=BATCH_CALL_TOOL, args={})
    _, routing = await router.schemas(agent, None)

    await executor.run([call], agent=agent, ctx=None, routing=routing)
    bridge = router.bridges[BATCH_CALL_TOOL]

    assert "read_file_tool" in bridge.names
    assert BATCH_CALL_TOOL not in bridge.names, "a program may not start another program"

    output = await bridge.call("read_file_tool", {"path": "x"})
    assert output == "read_file_tool ran"
    # The sub-call went through the executor, so it is in the router's log like any other.
    assert router.seen == [BATCH_CALL_TOOL, "read_file_tool"]


@pytest.mark.asyncio
async def test_a_program_cannot_call_a_name_it_was_never_shown():
    router = RecordingRouter()
    executor = ActionExecutor(router)
    agent = Agent(name="probe")
    _, routing = await router.schemas(agent, None)
    await executor.run(
        [ActionCall(id="prog", name=BATCH_CALL_TOOL, args={})],
        agent=agent, ctx=None, routing=routing,
    )
    bridge = router.bridges[BATCH_CALL_TOOL]

    with pytest.raises(LookupError):
        await bridge.call("rm_rf_tool", {})


@pytest.mark.asyncio
async def test_a_blocked_sub_call_raises_rather_than_returning_an_empty_success():
    """Silence would tell the program the tool did its work and had nothing to say."""

    class Refusing(RecordingRouter):
        def denial(self, call, routing, agent):
            return "read_only agent may not write" if call.name == "write_file_tool" else ""

    router = Refusing(names=["write_file_tool", BATCH_CALL_TOOL])
    executor = ActionExecutor(router)
    agent = Agent(name="probe", permission_mode="read_only")
    _, routing = await router.schemas(agent, None)
    await executor.run(
        [ActionCall(id="prog", name=BATCH_CALL_TOOL, args={})],
        agent=agent, ctx=None, routing=routing,
    )
    bridge = router.bridges[BATCH_CALL_TOOL]

    with pytest.raises(RuntimeError):
        await bridge.call("write_file_tool", {"path": "x"})


@pytest.mark.asyncio
async def test_the_calling_convention_is_absent_unless_a_program_can_be_run():
    """An agent that cannot run one has no use for the block, and would pay for it."""
    without = Agent(name="plain", capability_allowlists={"tool": ["read_file_tool"]})
    assert await without.code_mode_section() == ""


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


def _ctx(**extra):
    return SimpleNamespace(id="child-session", extra=dict(extra), parent_session_id="p")


def test_a_child_is_told_the_scope_it_will_be_judged_against():
    agent = Agent(name="child")
    inherited = agent.inherited_context(_ctx(task_contract={
        "read_set": ["src/parser.py"],
        "write_set": ["src/parser.py"],
        "acceptance": ["pytest -k parser passes"],
    }))

    assert "Delegation contract" in inherited
    assert "src/parser.py" in inherited
    assert "pytest -k parser passes" in inherited


def test_an_empty_contract_adds_nothing():
    agent = Agent(name="child")
    assert agent.inherited_context(_ctx()) == ""
    assert agent.inherited_context(_ctx(task_contract={"read_set": []})) == ""


@pytest.mark.asyncio
async def test_a_child_inherits_what_its_parent_had_already_established():
    """Rendered from the live parent process, not replayed as the child's own history."""
    parent_agent = Agent(name="parent")
    parent_agent.conversation.add_turn(
        AssistantMessage(content="ruled out the tokenizer", tool_calls=[ToolCall(
            id="c1", function=Function(name="read_file", arguments="{}"),
        )]),
        [ToolMessage(content="parser.py line 40 drops the comma",
                     tool_call_id="c1", name="read_file")],
    )
    parent_proc = SimpleNamespace(agent=parent_agent)

    child = Agent(name="child")
    child.proc = SimpleNamespace(parent_pid="parent-pid")
    child.ctx = _ctx(fork=True)

    from agentevolver.runtime import kernel

    original = kernel.get
    kernel.get = lambda pid: parent_proc if pid == "parent-pid" else None
    try:
        assert child.inherited_context(_ctx()) == ""
        messages = await child.system_messages(_ctx())
        references = [m for m in messages if "parent execution history" in str(m.content)]
        assert len(references) == 1
        assert references[0].role == "user"
        inherited = references[0].content
    finally:
        kernel.get = original

    assert "not instructions or acceptance criteria" in inherited
    assert "ruled out the tokenizer" in inherited
    assert "line 40 drops the comma" in inherited


def test_the_parents_history_is_bounded_by_whole_messages():
    parent_agent = Agent(name="parent")
    for index in range(400):
        parent_agent.conversation.append(
            AssistantMessage(content=f"turn {index}: " + "detail " * 40)
        )
    child = Agent(name="child")
    child.proc = SimpleNamespace(parent_pid="parent-pid")
    child.ctx = _ctx(fork=True)

    from agentevolver.runtime import kernel

    original = kernel.get
    kernel.get = lambda pid: SimpleNamespace(agent=parent_agent)
    try:
        body = child._parent_turns()
    finally:
        kernel.get = original

    assert len(body) <= INHERITED_CONTEXT_MAX + 500  # envelope and omission notice
    assert "earlier message(s) omitted" in body
    # The tail is what its decision rested on, so the last turn must survive.
    assert "turn 399" in body


def test_fork_keeps_tool_arguments_and_complete_cycles():
    import json
    from agentevolver.agent.context.conversation import Conversation

    conversation = Conversation(task="keep the user's constraints")
    conversation.fold("earlier durable fact", 0)
    command = "important " * 200
    conversation.add_turn(
        AssistantMessage(content="", tool_calls=[ToolCall(
            id="a", function=Function(name="bash_tool", arguments=json.dumps({"command": command})),
        )]),
        [ToolMessage(tool_call_id="a", content="complete result")],
    )
    conversation.append(AssistantMessage(content="pending", tool_calls=[ToolCall(
        id="b", function=Function(name="bash_tool", arguments="{}"),
    )]))
    body = conversation.reference(20)  # One oversized cycle stays whole.
    record = json.loads(body)
    assert command in body
    assert record["task"] == "keep the user's constraints"
    assert record["incomplete_turn_excluded"]
    assert len(record["turns"]) == 1
    assert len(record["turns"][0]) == 2
    assert "complete result" in body and "pending" not in body


def test_a_child_context_carries_lineage_and_scope_but_not_the_parents_run():
    """Sharing the parent's context would give two agents one session, and one memory."""
    from agentevolver.agent.loop.router import CapabilityRouter

    parent_ctx = SimpleNamespace(
        id="parent-session",
        extra={
            "plugin_allowlist": ["arxiv"],       # scoping the parent chose: travels
            "loaded_capabilities": {"x": ["y"]},  # the parent's own run state: does not
        },
    )
    brief = {
        "task": "fix the parser",
        "files": ["spec.md"],
        "read_set": ["src/parser.py"],
        "acceptance": ["tests pass"],
    }
    child_ctx = CapabilityRouter()._child_context(
        brief, SimpleNamespace(name="meta_agent"), parent_ctx,
    )

    assert child_ctx.id != "parent-session"
    assert child_ctx.parent_session_id == "parent-session"
    assert child_ctx.extra["task_contract"] == {
        "read_set": ["src/parser.py"], "acceptance": ["tests pass"],
    }
    assert child_ctx.extra["task_files"] == ["spec.md"]
    assert child_ctx.extra["plugin_allowlist"] == ["arxiv"]
    assert "loaded_capabilities" not in child_ctx.extra


@pytest.mark.parametrize("fork", [None, False, True])
def test_parent_history_requires_this_dispatch_to_opt_in(monkeypatch, fork):
    from agentevolver.runtime import kernel

    parent = Agent(name="parent")
    parent.conversation.append(AssistantMessage(content="private parent findings"))
    brief = {"task": "independent work"}
    if fork is not None:
        brief["fork"] = fork
    child = Agent(name="child")
    # A parent having opted in must not automatically opt its own children in.
    child.ctx = CapabilityRouter._child_context(brief, parent, _ctx(fork=True))
    child.proc = SimpleNamespace(parent_pid="parent-pid")
    reads = []

    def get(pid):
        reads.append(pid)
        return SimpleNamespace(agent=parent)

    monkeypatch.setattr(kernel, "get", get)
    assert child.ctx.extra["fork"] is (fork is True)
    assert ("private parent findings" in child._parent_turns()) is (fork is True)
    assert bool(reads) is (fork is True)


@pytest.mark.parametrize("value", ["false", "true", 1, None])
def test_dispatch_rejects_non_boolean_fork(value):
    from agentevolver.agent.server import validate_dispatch_input

    with pytest.raises(ValueError, match="fork must be a boolean"):
        validate_dispatch_input({"task": "independent work", "fork": value})


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,expected", [(7, 7), (1000, 100)])
@pytest.mark.parametrize("allow_override", [True, False])
async def test_dispatch_applies_model_budget_reasoning_and_environment(monkeypatch, budget, expected, allow_override):
    child = Agent(name="child", model_name="template", max_token=100,
                  allow_token_budget_override=allow_override)
    captured = {}

    async def build(name):
        return child

    async def spawn(agent, task, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid="child-pid")

    router = CapabilityRouter(kernel=SimpleNamespace(spawn=spawn))
    monkeypatch.setattr(router, "_build_child", build)
    call = ActionCall(id="c", name="child", args={
        "task": "go", "model": "chosen", "reasoning_effort": "high",
        "token_budget": budget, "environment_allowlist": [], "run_in_background": True,
    })
    result = await router._invoke_agent(call, ("agent", "child"), Agent(), _ctx())
    assert result.ok
    assert child.model_name == "chosen"
    assert child.max_token == (expected if allow_override else 100)
    child.ctx = captured["ctx"]
    assert child.request_input([], [])["reasoning_effort"] == "high"
    assert child.ctx.extra["environment_allowlist"] == []
    assert "environment_allowlist" in child.ctx.extra["_granted_allowlists"]


@pytest.mark.parametrize("args", [
    {"token_budget": True}, {"token_budget": 0}, {"model": ""},
    {"environment_allowlist": "browser"}, {"run_in_background": "false"},
    {"isolate_workspace": True}, {"isolate_worktree": True, "continuable": True},
])
def test_unsupported_or_invalid_dispatch_is_rejected(args):
    from agentevolver.agent.server import validate_dispatch_input
    with pytest.raises(ValueError):
        validate_dispatch_input({"task": "go", **args})


@pytest.mark.asyncio
async def test_cancelled_worktree_creation_cleans_registered_tree(bound_session, monkeypatch):
    import asyncio
    import subprocess
    from agentevolver.paths import P, path_manager
    from agentevolver.sandbox import worktree as module

    source = bound_session["workspace"]
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost",
                    "commit", "--allow-empty", "-m", "base"], check=True, capture_output=True)
    original = module._git

    async def interrupted(cwd, *args, **kwargs):
        result = await original(cwd, *args, **kwargs)
        if args[:2] == ("worktree", "add"):
            assert result[0] == 0
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(module, "_git", interrupted)
    storage = bound_session["log"]
    with pytest.raises(asyncio.CancelledError):
        await module.IsolatedWorktree.create(str(source), str(storage), "cancelled")
    target = path_manager.under(storage, P.LOG_WORKTREE, thread_id="cancelled")
    assert not target.exists()
    code, listing, _ = await original(source, "worktree", "list", "--porcelain")
    assert code == 0 and str(target) not in listing


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_worktree_dispatch_keeps_parent_clean_and_archives_patch(bound_session, monkeypatch, background):
    import subprocess
    from pathlib import Path
    from agentevolver.paths import P, path_manager
    from agentevolver.permission import Operation, PermissionRequest, permission_manager
    from agentevolver.response.types import Response, ResponseType
    from agentevolver.runtime.kernel import Kernel

    source = bound_session["workspace"]
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    (source / "file.txt").write_text("committed\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost",
                    "commit", "-m", "base"], check=True, capture_output=True)
    (source / "file.txt").write_text("parent dirty\n")
    (source / "parent-new.txt").write_text("untracked parent\n")
    class Worker(Agent):
        async def __call__(self, task="", files=None, ctx=None, **kwargs):
            workspace = path_manager.get(P.SESSION_WORKSPACE)
            assert workspace != source and str(workspace) == ctx.extra["execution_cwd"]
            assert (workspace / "file.txt").read_text() == "parent dirty\n"
            assert (workspace / "parent-new.txt").read_text() == "untracked parent\n"
            allowed = permission_manager.check_declared("writer", PermissionRequest(
                op=Operation.WRITE, target=str(workspace / "file.txt")), mode="workspace_write")
            assert allowed.allowed, allowed.reason
            (workspace / "file.txt").write_text("child changed\n")
            (workspace / "child-new.txt").write_text("child new\n")
            return Response(type=ResponseType.AGENT, success=True, message="complete")
    kernel = Kernel()
    parent = Agent()
    router = CapabilityRouter(kernel=kernel)
    async def build(name):
        return Worker(name=name)
    monkeypatch.setattr(router, "_build_child", build)
    try:
        await kernel.spawn(parent, resident=True, start_idle=True)
        with permission_manager.scope("workspace_write", workspace=str(source)):
            result = await router._invoke_agent(ActionCall(id="call", name="worker", args={
                "task": "edit", "isolate_worktree": True, "background": background,
            }), ("agent", "worker"), parent, _ctx())
        assert result.ok, result.error
        proc = kernel.get(result.extra["pid"])
        await kernel.wait(proc, timeout=5)
        assert proc.exit_status.value == "done", proc.error
        assert not proc.cleanup_errors
        patch = Path(proc.artifacts["patch"])
        assert "child changed" in patch.read_text() and "child-new.txt" in patch.read_text()
        assert (source / "file.txt").read_text() == "parent dirty\n"
        assert not (source / "child-new.txt").exists()
        assert not (patch.parent / "workspace").exists()
        assert path_manager.get(P.SESSION_WORKSPACE) == source
    finally:
        await kernel.shutdown(timeout=5)


@pytest.mark.asyncio
async def test_failed_child_reason_reaches_parent_tool_message(monkeypatch):
    from agentevolver.runtime.states import ExitStatus

    async def build(name):
        return Agent(name=name)

    async def spawn(*args, **kwargs):
        return SimpleNamespace(pid="failed-child", exit_status=ExitStatus.FAILED)

    async def wait(proc):
        return SimpleNamespace(success=False, message="Token limit reached")

    router = CapabilityRouter(kernel=SimpleNamespace(spawn=spawn, wait=wait))
    monkeypatch.setattr(router, "_build_child", build)
    result = await router._invoke_agent(
        ActionCall(id="c", name="child", args={"task": "go"}),
        ("agent", "child"), Agent(), _ctx(),
    )
    assert not result.ok
    assert "Token limit reached" in result.as_message().text


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_capability_registered_mid_run_is_announced(monkeypatch):
    """Otherwise the model works around a gap it no longer has."""
    from agentevolver.extension import extension_manager

    guard = CapabilityChanges()
    agent = Agent(name="probe")

    revision = {"value": 1}
    monkeypatch.setattr(
        type(extension_manager), "capability_revision",
        property(lambda self: revision["value"]),
    )

    agent._routing = {"read_file_tool": ("tool", "read_file_tool")}
    assert await guard(agent, 0) == ""            # first step only records

    assert await guard(agent, 1) == ""            # unchanged revision says nothing

    revision["value"] = 2
    agent._routing = {
        "read_file_tool": ("tool", "read_file_tool"),
        "calculator_tool": ("tool", "calculator_tool"),
    }
    note = await guard(agent, 2)
    assert "now available: calculator_tool" in note

    revision["value"] = 3
    agent._routing = {"calculator_tool": ("tool", "calculator_tool")}
    note = await guard(agent, 3)
    assert "no longer available, do not call: read_file_tool" in note


@pytest.mark.asyncio
async def test_the_announcement_rides_in_the_volatile_layer():
    """Past the cache breakpoint: saying it must not cost the session's cached prefix."""
    agent = Agent(name="probe")
    agent.middleware = [CapabilityChanges()]
    note = await agent.on_step(0)
    # Whatever it says, it comes back as a live block rather than being written into the
    # conversation or the fixed layer.
    assert isinstance(note, str)
    assert agent.conversation.items == []


# ---------------------------------------------------------------------------
# A blocked child is visible while it is blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalating_is_announced_before_the_child_starts_waiting(monkeypatch):
    """`ON_ESCALATE` was declared and never raised, so a parked child left no trace.

    Announced before the wait on purpose: the fact worth observing is that a process is
    blocked, and a run that dies waiting would otherwise never record that it asked.
    """
    from agentevolver.agent.loop import events as bus
    from agentevolver.hook.events import HookEvent
    from agentevolver.runtime import kernel
    from agentevolver.tool.default.coordination import escalate as module

    seen: List[Any] = []

    async def broadcast(event, payload=None, *, ctx=None):
        seen.append((event, payload or {}))

    order: List[str] = []

    async def ask_parent(text, timeout=None):
        order.append("waited")
        return "use the shell"

    process = SimpleNamespace(
        name="code_agent", pid="child-pid", session_id="s1",
        parent_pid="parent-pid", ctx=None, ask_parent=ask_parent,
    )
    monkeypatch.setattr(bus.events, "broadcast", broadcast)
    monkeypatch.setattr(kernel, "get", lambda pid: process)

    answer = await module._ask_parent(
        _ctx(process_pid="child-pid"), "no deploy capability", "tried three things", ""
    )

    assert answer == "use the shell"
    assert [event for event, _ in seen] == [HookEvent.ON_ESCALATE]
    body = seen[0][1]
    assert body["task_id"] == "child-pid" and body["parent_session_id"] == "parent-pid"
    assert body["reason"] == "no deploy capability"
    assert order == ["waited"]


@pytest.mark.asyncio
async def test_a_standalone_run_announces_nothing_and_does_not_wait(monkeypatch):
    """No parent is an answer, not an escalation. Announcing one would be a false fact."""
    from agentevolver.agent.loop import events as bus
    from agentevolver.runtime import kernel
    from agentevolver.tool.default.coordination import escalate as module

    seen: List[Any] = []

    async def broadcast(event, payload=None, *, ctx=None):
        seen.append(event)

    monkeypatch.setattr(bus.events, "broadcast", broadcast)
    monkeypatch.setattr(kernel, "get", lambda pid: None)

    answer = await module._ask_parent(_ctx(process_pid="orphan"), "blocked", "", "")
    assert "No parent to escalate to" in answer
    assert seen == []


# ---------------------------------------------------------------------------
# What an evolution run needs to know about its own target
# ---------------------------------------------------------------------------


def test_a_child_is_told_which_kind_of_component_it_is_working_on():
    """`target_type` / `target_name` are chosen per dispatch, and must cross with it.

    The evolution roles read their target from the context rather than from the task
    text, because a generate run's target does not exist yet and so cannot be looked up
    by name. The dispatch schema has always declared both fields — its own description
    says "unstated, the run cannot install what it built" — and `_child_context` built
    the child's extras only from what the PARENT had inherited, so neither ever arrived.

    Every generate run therefore ended `target_type must be one of ...; got ''` at the
    moment it tried to register what it had written. Measured on a live run: 47 steps,
    $3.46, and a manifest of `{"components": []}`.
    """
    parent = SimpleNamespace(name="website_builder_agent")
    brief = {
        "task": "write a tool that renders the lore timeline",
        "target_type": "tool",
        "target_name": "lore_timeline_tool",
    }
    child = CapabilityRouter._child_context(brief, parent, _ctx())

    assert child.extra["target_type"] == "tool"
    assert child.extra["target_name"] == "lore_timeline_tool"


def test_an_ordinary_dispatch_carries_no_target():
    """Only an evolution dispatch names one; a worker must not inherit a stale target."""
    parent = SimpleNamespace(name="meta_agent")
    child = CapabilityRouter._child_context({"task": "read the log"}, parent, _ctx())
    assert "target_type" not in child.extra
    assert "target_name" not in child.extra


def test_a_blank_target_is_not_carried_as_an_empty_string():
    """An empty value is the same as absent; carrying "" would pass a `key in extra`
    check and then fail the enum, which is the harder failure to read."""
    parent = SimpleNamespace(name="meta_agent")
    brief = {"task": "x", "target_type": "  ", "target_name": ""}
    child = CapabilityRouter._child_context(brief, parent, _ctx())
    assert "target_type" not in child.extra
    assert "target_name" not in child.extra
