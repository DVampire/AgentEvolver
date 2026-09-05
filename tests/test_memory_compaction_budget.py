"""Portable compaction must respect one aggregate input budget.

Long traces can contain hundreds of individually small records.  The packer therefore
budgets separators and records together instead of applying a per-record minimum that
silently expands the request beyond the configured ceiling.
"""

import pytest


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
