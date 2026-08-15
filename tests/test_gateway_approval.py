"""A Gateway approval is real, one-shot consent bound to one immutable Tool call."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.gateway.protocol import GatewayCommand
from agentevolver.gateway.service import AgentGateway
from agentevolver.response import Response, ResponseType
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.execution import ToolExecution, ToolPolicyDecision
from agentevolver.tool.types import Tool, ToolContext


async def _session(gateway: AgentGateway, command_id: str = "create") -> str:
    response = await gateway.handle(GatewayCommand(
        id=command_id, method="session.create", params={},
    ))
    assert response.ok
    return str(response.result["session_id"])


def _execution(
    project_id: str,
    *,
    conversation_id: str = "conversation-1",
    arguments=None,
) -> ToolExecution:
    return ToolExecution.create(
        name="deploy_tool",
        version="2.0.0",
        arguments=arguments or {"target": "staging"},
        ctx=SimpleNamespace(
            id=conversation_id,
            name="release_agent",
            extra={"project_id": project_id},
        ),
        context={
            "call_id": "call-1",
            "root_call_id": "call-1",
            "task_id": "task-1",
            "step_number": 3,
            "action_index": 0,
        },
    )


@pytest.mark.asyncio
async def test_allow_once_resumes_the_exact_call_and_cannot_be_replayed():
    gateway = AgentGateway()
    queue = await gateway.subscribe()
    project_id = await _session(gateway)
    await queue.get()  # session.created

    waiting = asyncio.create_task(
        gateway._approvals.request(
            _execution(project_id), "Deploy the tested build to staging?",
        )
    )
    requested = await asyncio.wait_for(queue.get(), timeout=1)
    assert requested.type == "approval.requested"
    approval_id = requested.payload["approval_id"]
    assert requested.session_id == project_id
    assert requested.conversation_id == "conversation-1"

    response = await gateway.handle(GatewayCommand(
        id="approve",
        method="approval.respond",
        params={
            "session_id": project_id,
            "approval_id": approval_id,
            "decision": "allow_once",
        },
    ))
    assert response.ok and response.result["delivered"] is True
    assert await waiting is True

    duplicate = await gateway.handle(GatewayCommand(
        id="approve-again",
        method="approval.respond",
        params={
            "session_id": project_id,
            "approval_id": approval_id,
            "decision": "allow_once",
        },
    ))
    assert duplicate.ok and duplicate.result["delivered"] is False


@pytest.mark.asyncio
async def test_pending_approval_is_listable_after_reconnect_and_session_fenced():
    gateway = AgentGateway()
    project_id = await _session(gateway, "create-a")
    other_project = await _session(gateway, "create-b")
    waiting = asyncio.create_task(
        gateway._approvals.request(_execution(project_id), "Confirm deployment")
    )
    await asyncio.sleep(0)

    listed = await gateway.handle(GatewayCommand(
        id="list", method="approval.list", params={"session_id": project_id},
    ))
    assert listed.ok and len(listed.result["approvals"]) == 1
    approval_id = listed.result["approvals"][0]["approval_id"]

    crossed = await gateway.handle(GatewayCommand(
        id="cross-session",
        method="approval.respond",
        params={
            "session_id": other_project,
            "approval_id": approval_id,
            "decision": "allow_once",
        },
    ))
    assert crossed.ok and crossed.result["delivered"] is False
    assert not waiting.done()

    rejected = await gateway.handle(GatewayCommand(
        id="reject",
        method="approval.respond",
        params={
            "session_id": project_id,
            "approval_id": approval_id,
            "decision": "reject",
            "comment": "The change window is closed.",
        },
    ))
    assert rejected.ok and rejected.result["delivered"] is True
    assert await waiting is False


@pytest.mark.asyncio
async def test_approval_payload_binds_arguments_without_disclosing_values(monkeypatch):
    gateway = AgentGateway()
    project_id = await _session(gateway)
    traced = []

    async def capture(event):
        traced.append(event)
        return True

    monkeypatch.setattr("agentevolver.trace.server.trace_manager.emit", capture)
    secret = "super-secret-cookie-value"
    waiting = asyncio.create_task(gateway._approvals.request(
        _execution(
            project_id,
            arguments={"command": "deploy", "authorization": secret},
        ),
        "Run the reviewed deployment command?",
    ))
    await asyncio.sleep(0)
    record = gateway._approvals.pending(project_id)[0]
    public = record.public()
    assert public["argument_names"] == ["authorization", "command"]
    assert len(public["arguments_sha256"]) == 64
    assert secret not in str(public)

    await gateway._approvals.respond(
        session_id=project_id,
        approval_id=record.id,
        approved=False,
        decision="reject",
    )
    assert await waiting is False
    assert [event.label for event in traced] == [
        "approval.requested", "approval.responded",
    ]
    assert secret not in str([event.to_dict() for event in traced])
    assert all(event.ignorable is False for event in traced)


@pytest.mark.asyncio
async def test_timeout_and_gateway_shutdown_both_fail_closed():
    gateway = AgentGateway()
    project_id = await _session(gateway)
    gateway._approvals.set_timeout(0.01)

    expired = await gateway._approvals.request(
        _execution(project_id), "Nobody answers this request",
    )
    assert expired is False
    assert gateway._approvals.pending(project_id) == []

    gateway._approvals.set_timeout(60)
    waiting = asyncio.create_task(gateway._approvals.request(
        _execution(project_id), "Gateway stops while this is pending",
    ))
    await asyncio.sleep(0)
    await gateway._approvals.cancel_all("gateway_stopped")
    assert await waiting is False


@pytest.mark.asyncio
async def test_invalid_decision_is_rejected_without_settling_the_request():
    gateway = AgentGateway()
    project_id = await _session(gateway)
    waiting = asyncio.create_task(gateway._approvals.request(
        _execution(project_id), "Confirm",
    ))
    await asyncio.sleep(0)
    approval_id = gateway._approvals.pending(project_id)[0].id

    invalid = await gateway.handle(GatewayCommand(
        id="invalid",
        method="approval.respond",
        params={
            "session_id": project_id,
            "approval_id": approval_id,
            "decision": "always_allow",
        },
    ))
    assert not invalid.ok and invalid.error.code == "invalid_request"
    assert not waiting.done()
    await gateway._approvals.cancel_all()
    assert await waiting is False


@pytest.mark.asyncio
async def test_tool_body_runs_only_after_approval_and_the_final_checkpoint(
    tmp_path, monkeypatch,
):
    order = []

    class DeployTool(Tool):
        name: str = "deploy_tool"
        description: str = "Deploy a build."
        mutates: bool = True

        async def __call__(self, target: str, **kwargs):
            order.append("body")
            return Response(type=ResponseType.TOOL, success=True, message=target)

    manager = ToolContextManager(base_dir=str(tmp_path))

    async def get_info(name):
        return SimpleNamespace(version="2.0.0", instance=DeployTool())

    manager.get_info = get_info
    manager.guard(lambda execution: ToolPolicyDecision.ask("Approve deployment"))
    gateway = AgentGateway()
    project_id = await _session(gateway)
    manager.set_approval_resolver(gateway._approvals.request)

    async def checkpoint(*args, **kwargs):
        order.append("checkpoint")
        return True

    monkeypatch.setattr("agentevolver.trace.checkpoint.checkpoint_trace", checkpoint)
    running = asyncio.create_task(manager(
        name="deploy_tool",
        input={"target": "staging"},
        ctx=ToolContext(
            id="conversation-1", name="release_agent",
            extra={"project_id": project_id},
        ),
        execution_context={"task_id": "task-1", "call_id": "call-1"},
    ))
    await asyncio.sleep(0)
    record = gateway._approvals.pending(project_id)[0]
    assert order == []

    delivered = await gateway._approvals.respond(
        session_id=project_id,
        approval_id=record.id,
        approved=True,
        decision="allow_once",
    )
    result = await running
    assert delivered is True and result.success is True
    assert order == ["checkpoint", "body"]


def test_approval_resolver_disposer_cannot_remove_a_newer_resolver():
    manager = ToolContextManager()
    first = lambda execution, reason: True
    second = lambda execution, reason: False
    dispose_first = manager.set_approval_resolver(first)
    manager.set_approval_resolver(second)
    dispose_first()
    assert manager._execution_pipeline._approval_resolver is second
