"""One Tool entry point owns policy, failure semantics, identity, and observation."""

from types import SimpleNamespace

import pytest

from agentevolver.hook.default.trace import TraceHook
from agentevolver.hook.types import HookContext, HookEvent
from agentevolver.permission import (
    Operation,
    PermissionMode,
    PermissionRequest,
    permission_manager,
)
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.execution import ToolPolicyDecision
from agentevolver.tool.types import Tool


class EchoTool(Tool):
    name: str = "echo_tool"
    description: str = "Return the supplied value."
    call_timeout_seconds: float = 2

    async def __call__(self, value: str, **kwargs) -> Response:
        return Response(type=ResponseType.TOOL, success=True, message=value)


def manager_for(tmp_path, instance=None):
    manager = ToolContextManager(base_dir=str(tmp_path), default_timeout=2)
    selected = instance or EchoTool()

    async def get_info(name):
        if name == selected.name:
            return SimpleNamespace(version="3.2.1", instance=selected)
        return None

    manager.get_info = get_info
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, mutates, allowed", [
    ("workspace_write", None, False), ("workspace_write", True, False),
    ("workspace_write", False, True), ("read_only", None, True),
    ("read_only", True, False),
])
async def test_read_only_scope_rejects_tools_without_operation_contract(tmp_path, mode, mutates, allowed):
    called = []
    class NoIntent(EchoTool):
        async def __call__(self, value, **kwargs):
            called.append(value)
            return await super().__call__(value, **kwargs)
    tool = NoIntent(permission_mode=mode, mutates=mutates)
    manager = manager_for(tmp_path, tool)
    with permission_manager.scope("read_only", workspace=str(tmp_path)):
        response = await manager(name=tool.name, input={"value": "x"})
    assert response.success is allowed
    assert bool(called) is allowed


def test_real_router_reads_loaded_argument_dependent_effects(tmp_path, monkeypatch):
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.agent.loop.decision import ActionCall
    from agentevolver.tool import tool_manager

    class Mixed(EchoTool):
        def will_mutate(self, arguments):
            return arguments["write"]
    manager = manager_for(tmp_path)
    manager._tool_configs["mixed"] = SimpleNamespace(instance=Mixed())
    monkeypatch.setattr(tool_manager, "tool_context_manager", manager)
    router = CapabilityRouter()
    routing = {"mixed": ("tool", "mixed")}
    for effect, expected in ((True, False), (False, True), (None, None)):
        call = ActionCall(id="call", name="mixed", args={"write": effect})
        assert router.read_only(call, routing) is expected
    monkeypatch.setattr(tool_manager, "tool_context_manager", None)
    assert router.read_only(call, routing) is None
    assert tool_manager.tool_context_manager is None  # No implicit initialization.


@pytest.mark.asyncio
async def test_identity_and_arguments_cannot_be_rewritten_by_a_guard(tmp_path):
    manager = manager_for(tmp_path)
    seen = []

    def first(execution):
        detached = execution.arguments
        detached["value"] = "rewritten"
        seen.append((execution.token, execution.call_id, execution.root_call_id))

    def second(execution):
        assert execution.arguments == {"value": "original"}
        seen.append((execution.token, execution.call_id, execution.root_call_id))

    manager.guard(first)
    manager.guard(second)
    response = await manager(
        name="echo_tool",
        input={"value": "original"},
        ctx=SimpleNamespace(id="session-1", name="agent", extra={}),
        execution_context={
            "call_id": "sub-1",
            "root_call_id": "root-1",
            "parent_call_id": "parent-1",
            "task_id": "task-1",
            "step_number": 4,
            "action_index": 2,
        },
    )

    assert response.success is True and response.message == "original"
    assert seen[0] == seen[1]
    meta = response.extra["execution"]
    assert meta["schema_version"] == 2
    assert meta["project_id"] == "session-1"
    assert meta["tool_version"] == "3.2.1"
    assert (meta["call_id"], meta["root_call_id"], meta["parent_call_id"]) == (
        "sub-1",
        "root-1",
        "parent-1",
    )
    assert (meta["session_id"], meta["task_id"], meta["step_number"]) == (
        "session-1",
        "task-1",
        4,
    )


@pytest.mark.asyncio
async def test_monotonic_denial_skips_the_body_and_has_a_stable_code(tmp_path):
    called = False

    class MustNotRun(EchoTool):
        async def __call__(self, value: str, **kwargs) -> Response:
            nonlocal called
            called = True
            return await super().__call__(value, **kwargs)

    manager = manager_for(tmp_path, MustNotRun())
    manager.guard(lambda execution: "dataset collection forbids this tool")
    response = await manager(name="echo_tool", input={"value": "x"})

    assert called is False
    assert response.success is False
    assert "dataset collection" in response.message
    assert response.extra["execution"]["error_code"] == "policy_denied"
    assert response.extra["execution"]["stage"] == "guard"


@pytest.mark.asyncio
async def test_tool_owned_permission_intent_is_checked_before_the_body(tmp_path):
    called = False

    class ShellLike(EchoTool):
        def permission_request(self, arguments, ctx=None):
            return PermissionRequest(
                op=Operation.BASH,
                target=str(arguments.get("value") or ""),
            )

        async def __call__(self, value: str, **kwargs):
            nonlocal called
            called = True
            return await super().__call__(value, **kwargs)

    permission_manager.register("echo_tool", mode=PermissionMode.READ_ONLY)
    try:
        response = await manager_for(tmp_path, ShellLike())(
            name="echo_tool",
            input={"value": 'python -c \'open("x","w")\''},
        )
    finally:
        permission_manager.unregister("echo_tool")

    assert called is False
    assert response.success is False
    assert response.extra["execution"]["error_code"] == "policy_denied"
    assert "Permission denied" in response.message


@pytest.mark.asyncio
async def test_destructive_permission_warning_routes_through_approval(tmp_path):
    called = False

    class ShellLike(EchoTool):
        permission_mode: str = "danger_full_access"

        def permission_request(self, arguments, ctx=None):
            return PermissionRequest(
                op=Operation.BASH,
                target=str(arguments.get("value") or ""),
            )

        async def __call__(self, value: str, **kwargs):
            nonlocal called
            called = True
            return await super().__call__(value, **kwargs)

    permission_manager.register("echo_tool", mode=PermissionMode.DANGER_FULL_ACCESS)
    try:
        manager = manager_for(tmp_path, ShellLike())
        denied = await manager(name="echo_tool", input={"value": "rm -rf cache"})
        manager.set_approval_resolver(lambda execution, reason: True)
        approved = await manager(name="echo_tool", input={"value": "rm -rf cache"})
    finally:
        permission_manager.unregister("echo_tool")

    assert denied.success is False
    assert denied.extra["execution"]["error_code"] == "approval_unavailable"
    assert approved.success is True
    assert called is True


@pytest.mark.asyncio
async def test_broken_permission_intent_fails_closed_as_a_guard_error(tmp_path):
    class BrokenPolicy(EchoTool):
        def permission_request(self, arguments, ctx=None):
            raise RuntimeError("policy backend unavailable")

    response = await manager_for(tmp_path, BrokenPolicy())(
        name="echo_tool",
        input={"value": "x"},
    )

    assert response.success is False
    assert response.extra["execution"]["error_code"] == "guard_error"
    assert "failed closed" in response.message


@pytest.mark.asyncio
async def test_an_invalid_guard_decision_fails_closed_instead_of_crashing(tmp_path):
    manager = manager_for(tmp_path)
    manager.guard(lambda execution: {"allowed": True})

    response = await manager(name="echo_tool", input={"value": "x"})

    assert response.success is False
    assert response.extra["execution"]["error_code"] == "guard_error"
    assert "unsupported decision" in response.message


@pytest.mark.asyncio
async def test_an_ask_without_an_approval_channel_fails_closed(tmp_path):
    manager = manager_for(tmp_path)
    manager.guard(lambda execution: ToolPolicyDecision.ask("confirm deployment"))

    response = await manager(name="echo_tool", input={"value": "x"})

    assert response.success is False
    assert response.extra["execution"]["error_code"] == "approval_unavailable"


@pytest.mark.asyncio
async def test_explicit_one_shot_approval_allows_the_same_guard(tmp_path):
    manager = manager_for(tmp_path)
    manager.guard(lambda execution: ToolPolicyDecision.ask("confirm deployment"))
    manager.set_approval_resolver(
        lambda execution, reason: (
            execution.tool_name == "echo_tool" and reason == "confirm deployment"
        )
    )

    response = await manager(name="echo_tool", input={"value": "approved"})

    assert response.success is True
    assert response.message == "approved"


@pytest.mark.asyncio
async def test_tool_exception_and_invalid_result_are_normalized(tmp_path):
    class Boom(EchoTool):
        async def __call__(self, value: str, **kwargs):
            raise LookupError("backend disappeared")

    failure = await manager_for(tmp_path, Boom())(
        name="echo_tool",
        input={"value": "x"},
    )
    assert failure.success is False
    assert failure.extra["execution"]["error_code"] == "execution_error"
    assert "LookupError" in failure.message and "backend disappeared" in failure.message

    class BadResult(EchoTool):
        async def __call__(self, value: str, **kwargs):
            return {"message": value}

    invalid = await manager_for(tmp_path, BadResult())(
        name="echo_tool",
        input={"value": "x"},
    )
    assert invalid.success is False
    assert invalid.extra["execution"]["error_code"] == "invalid_result"


@pytest.mark.asyncio
async def test_postprocessing_cannot_launder_a_tool_failure(tmp_path):
    class Refusal(EchoTool):
        async def __call__(self, value: str, **kwargs):
            return Response(type=ResponseType.TOOL, success=False, message="refused")

    manager = manager_for(tmp_path, Refusal())
    manager.postprocess(
        lambda execution, response: Response(
            type=ResponseType.TOOL,
            success=True,
            message="pretend it worked",
        )
    )
    response = await manager(name="echo_tool", input={"value": "x"})

    assert response.success is False
    assert response.extra["execution"]["error_code"] == "postprocess_error"
    assert "cannot replace failure with success" in response.message


@pytest.mark.asyncio
async def test_guard_disposer_and_result_observer_have_exact_lifetimes(tmp_path):
    manager = manager_for(tmp_path)
    observed = []
    dispose_guard = manager.guard(lambda execution: "temporary denial")
    dispose_observer = manager.observe(
        lambda execution, outcome, response: observed.append(
            (execution.tool_name, outcome.success, response.message)
        )
    )

    denied = await manager(name="echo_tool", input={"value": "first"})
    dispose_guard()
    allowed = await manager(name="echo_tool", input={"value": "second"})
    dispose_observer()
    await manager(name="echo_tool", input={"value": "third"})

    assert denied.success is False and allowed.success is True
    assert observed == [
        ("echo_tool", False, "temporary denial"),
        ("echo_tool", True, "second"),
    ]


@pytest.mark.asyncio
async def test_before_invoke_failure_is_classified_and_never_runs_the_effect():
    from agentevolver.tool.execution import ToolExecution, ToolExecutionPipeline

    pipeline = ToolExecutionPipeline()
    ran = False

    async def invoke():
        nonlocal ran
        ran = True

    async def prepare():
        raise OSError("checkpoint disk is unavailable")

    response = await pipeline.execute(
        ToolExecution.create(name="write", version="1", arguments={}),
        invoke,
        timeout=None,
        before_invoke=prepare,
    )

    assert not response.success and not ran
    assert response.extra["execution"]["stage"] == "prepare"
    assert response.extra["execution"]["error_code"] == "preparation_error"


@pytest.mark.asyncio
async def test_trace_keeps_the_pipeline_outcome_beside_the_tool_result(monkeypatch):
    emitted = []

    async def capture(event):
        emitted.append(event)

    monkeypatch.setattr("agentevolver.trace.server.trace_manager.emit", capture)
    execution_meta = {
        "schema_version": 1,
        "token": "execution-1",
        "call_id": "call-1",
        "root_call_id": "call-1",
        "tool_name": "echo_tool",
        "tool_version": "3.2.1",
        "success": False,
        "stage": "execute",
        "error_code": "timeout",
    }
    hook = TraceHook()
    await hook.handle(
        HookContext(
            id="session-1",
            name="trace_hook",
            input={
                "event": HookEvent.POST_ACTION,
                "agent_name": "agent",
                "task_id": "task-1",
                "step_number": 1,
                "action": {
                    "index": 0,
                    "id": "call-1",
                    "type": "tool",
                    "name": "echo_tool",
                    "args_parsed": {"value": "x"},
                },
                "action_result": "timed out",
                "error": "timed out",
                "execution_meta": execution_meta,
            },
        )
    )

    assert emitted[0].metadata["execution"] == execution_meta
    assert emitted[0].metadata["call_id"] == "call-1"
