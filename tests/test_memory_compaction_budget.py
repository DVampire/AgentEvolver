"""Portable compaction must respect one aggregate input budget.

Long traces can contain hundreds of individually small records.  The packer therefore
budgets separators and records together instead of applying a per-record minimum that
silently expands the request beyond the configured ceiling.
"""

import pytest


@pytest.mark.asyncio
async def test_summarizer_receives_current_task_and_session_for_accounting(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agentevolver.hook.default import compact as module
    from agentevolver.hook.types import HookContext

    model = AsyncMock(return_value=SimpleNamespace(
        success=True, message="Acceptance conditions: preserve existing public URLs.", usage={"input_tokens": 12}))
    monkeypatch.setattr(module, "model_manager", model)
    ctx = HookContext(id="session", name="compact", input={
        "task": "Preserve existing public URLs.", "items": ["Checked routing"], "model_name": "test",
        "max_output_tokens": 2048,
        "previous_summary": "Rejected candidate", "audit_feedback": "Incorrect plan path",
        "trace_context": {"agent_name": "builder", "task_id": "p", "step_number": 7}})
    result = await module.CompactHook().handle(ctx)
    kwargs = model.await_args.kwargs
    assert kwargs["ctx"] is ctx
    assert "Preserve existing public URLs." in kwargs["input"]["messages"][-1].text
    assert kwargs["input"]["trace_context"]["step_number"] == 7
    assert kwargs["input"]["max_output_tokens"] == 4096
    assert kwargs["input"]["reserved_output_tokens"] == 4096
    assert "within 2048 tokens" in kwargs["input"]["messages"][-1].text
    assert "Incorrect plan path" in kwargs["input"]["messages"][-1].text
    assert "Rejected candidate" in kwargs["input"]["messages"][-1].text
    assert result.usage == {"input_tokens": 12}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "omitted", "invented", "malformed"])
async def test_checkpoint_semantic_audit_requires_grounded_evidence(monkeypatch, mode):
    import json
    from types import SimpleNamespace
    from agentevolver.hook.default import compact as module

    audit = {"safe_to_replace": True, "omissions": [], "contradictions": [],
             "preserved": [{"source_quote": "keep old URLs", "checkpoint_quote": "keep old URLs"}]}
    if mode == "omitted":
        audit["omissions"] = ["keep old URLs"]
    if mode == "invented":
        audit["preserved"][0]["source_quote"] = "not present in source"
    async def model(**kwargs):
        return SimpleNamespace(success=True, usage={"input_tokens": 5},
                               message="not JSON" if mode == "malformed" else json.dumps(audit))
    monkeypatch.setattr(module, "model_manager", model)
    result = await module.CompactHook.verify(source="User says keep old URLs.",
                                             summary="Must keep old URLs.", model="fake")
    assert result.approved is (mode == "valid")
    assert result.usage == {"input_tokens": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("retained", ["Task: keep old URLs", ""])
async def test_audit_accounts_for_verbatim_context_without_accepting_invented_quotes(monkeypatch, retained):
    import json
    from types import SimpleNamespace
    from agentevolver.hook.default import compact as module

    async def model(**kwargs):
        data = json.loads(kwargs["input"]["messages"][-1].text)
        assert data["retained_context"] == retained
        return SimpleNamespace(success=True, usage=None, message=json.dumps({
            "safe_to_replace": True, "omissions": [], "contradictions": [],
            "preserved": [{"source_quote": "keep old URLs", "checkpoint_quote": "keep old URLs"}],
        }))
    monkeypatch.setattr(module, "model_manager", model)
    result = await module.CompactHook.verify(source="Earlier routing work", summary="Routing inspected.",
                                             model="fake", retained_context=retained)
    assert result.approved is bool(retained)


@pytest.mark.asyncio
async def test_failed_semantic_audit_preserves_original_history(monkeypatch):
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.message.types import AssistantMessage
    from agentevolver.hook.default.compact import CompactHook
    from agentevolver.hook.types import HookResult

    agent = Agent(retain_recent_steps=1)
    agent.conversation.extend([AssistantMessage(content="keep old URLs"), AssistantMessage(content="recent")])
    async def native(*args):
        return {"summary": "URLs may be removed"}
    async def rejected(**kwargs):
        assert "keep old URLs" in kwargs["source"]
        return HookResult(approved=False, output="Lost the URL preservation requirement")
    monkeypatch.setattr(Agent, "native_checkpoint", native)
    monkeypatch.setattr(CompactHook, "verify", rejected)
    moved, reason = await agent._fold("test")
    assert not moved and "semantic audit" in reason
    assert agent.conversation.checkpoint is None and agent.conversation.turns == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_approved", [True, False])
async def test_checkpoint_repairs_once_from_audit_without_bypassing_validation(monkeypatch, repair_approved):
    import json
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.hook.default.compact import CompactHook
    from agentevolver.hook.types import HookResult
    from agentevolver.message.types import AssistantMessage

    agent = Agent(retain_recent_steps=1)
    agent.conversation.append(AssistantMessage(content="Plan is /session/plan.md. " * 80))
    agent.conversation.note("Continue")
    agent.conversation.append(AssistantMessage(content="Recent exact turn"))
    original = [m.model_dump() for m in agent.conversation.items]
    audits, repairs = [], []

    async def native(*args):
        return {"summary": "Plan is /workspace/plan.md."}

    async def audit(**kwargs):
        audits.append(kwargs)
        assert "/session/plan.md" in kwargs["source"]
        assert "Recent exact turn" in kwargs["retained_context"]
        return HookResult(approved=bool(len(audits) == 2 and repair_approved), output=json.dumps({
            "omissions": [], "contradictions": ["Incorrect plan path"],
        }))

    async def repair(source, **kwargs):
        repairs.append(kwargs)
        assert "/workspace/plan.md" in kwargs["previous_summary"]
        assert "Incorrect plan path" in kwargs["audit_feedback"]
        return "Plan is /session/plan.md."

    monkeypatch.setattr(agent, "native_checkpoint", native)
    monkeypatch.setattr(agent, "text_checkpoint", repair)
    monkeypatch.setattr(CompactHook, "verify", audit)
    moved, _ = await agent._fold("full input")
    assert len(audits) == 2 and len(repairs) == 1
    assert moved is repair_approved
    if repair_approved:
        assert "/session/plan.md" in agent.conversation.checkpoint.text
        assert agent.conversation.items[-1].text == "Recent exact turn"
    else:
        assert [m.model_dump() for m in agent.conversation.items] == original
        assert agent.conversation.checkpoint is None

from agentevolver.memory.default.tiered import TieredMemory


def test_summary_refuses_to_omit_records_to_fit_the_budget():
    memory = TieredMemory(compact_input_tokens=128)
    items = [f"[source_seq={index}] " + ("x" * 300) for index in range(500)]

    with pytest.raises(ValueError, match="No history was omitted"):
        memory._pack_summary_items(items)
    assert len(items) == 500


def test_summary_preserves_every_character_when_source_fits():
    memory = TieredMemory(compact_input_tokens=128)
    items = ["first\n完整内容", "second\nimportant middle\nlast"]
    assert memory._pack_summary_items(items) == items


def test_early_pressure_uses_route_tools_and_output_reservation():
    from agentevolver.model.server import ModelManagerServer
    from agentevolver.model.types import ModelConfig
    from agentevolver.agent.context.assembler import ContextAssembler
    from agentevolver.agent.context.conversation import Conversation
    from agentevolver.message.types import AssistantMessage

    manager = ModelManagerServer()
    manager.model_context_manager.models["small"] = ModelConfig(
        model_name="small", model_id="small", model_type="responses", provider="test",
        context_window=2048, max_output_tokens=512,
    )
    conversation = Conversation(task="test")
    for _ in range(5):
        conversation.note("continue")
        conversation.append(AssistantMessage(content="done"))
    assembler = ContextAssembler(compact_after_turns=0, compact_body_tokens=0)
    request = {"messages": assembler.build(conversation), "tools": [{"description": "schema " * 5000}]}
    pressure = manager.measure("small", request)
    assert pressure["input_capacity_tokens"] == 1536
    assert pressure["over_capacity"]
    assert "capacity=" in assembler.fold_reason(conversation, request_pressure=pressure)
    assert request["tools"][0]["description"].endswith("schema ")
