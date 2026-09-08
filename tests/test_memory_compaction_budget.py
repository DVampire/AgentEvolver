"""Compaction uses completed summaries directly and bounds failed-call retries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_summarizer_receives_current_task_and_session_for_accounting(monkeypatch):
    from agentevolver.hook.default import compact as module
    from agentevolver.hook.types import HookContext

    summary = "### Established facts\n" + "Preserve public URLs. " * 600
    model = AsyncMock(return_value=SimpleNamespace(
        success=True, message=summary, usage={"input_tokens": 12}))
    monkeypatch.setattr(module, "model_manager", model)
    ctx = HookContext(id="session", name="compact", input={
        "task": "Preserve existing public URLs.", "items": ["Checked routing"],
        "existing_summary": "Routing has two public entrypoints.", "model_name": "test",
        "max_output_tokens": 2048,
        "trace_context": {"agent_name": "builder", "task_id": "p", "step_number": 7}})
    result = await module.CompactHook().handle(ctx)
    model.assert_awaited_once()
    kwargs = model.await_args.kwargs
    assert kwargs["ctx"] is ctx
    request = kwargs["input"]
    prompt = request["messages"][-1].text
    assert "Preserve existing public URLs." in prompt
    assert "Routing has two public entrypoints." in prompt
    assert request["trace_context"]["step_number"] == 7
    assert request["max_output_tokens"] == request["reserved_output_tokens"] == 8192
    assert request["operation"] == "compact"
    assert "about 2048 tokens" in prompt
    assert "Preserve public URLs. " * 599 in result.output
    assert result.usage == {"input_tokens": 12}


@pytest.mark.asyncio
async def test_agent_checkpoint_keeps_fixed_contract_and_recent_call_pair(monkeypatch):
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.hook.default import compact as module
    from agentevolver.hook.types import HookContext
    from agentevolver.message.types import AssistantMessage, SystemMessage, ToolCall, ToolMessage

    model = AsyncMock(return_value=SimpleNamespace(
        success=True, message="### Established facts\nParser accepts a zero offset.\n\n"
        "### Next action\nConnect the caller to parse(offset=0).", usage=None,
    ))
    monkeypatch.setattr(module, "model_manager", model)
    agent = Agent(retain_recent_steps=1)
    agent.conversation.task = "Preserve the public parser API."
    agent.conversation.system = [SystemMessage(content="Stable rules")]
    agent.conversation.extend([
        AssistantMessage(content="Parser implemented"),
        AssistantMessage(content="", tool_calls=[ToolCall(
            id="read", function={"name": "bash_tool", "arguments": '{"command":"inspect caller"}'},
        )]),
        ToolMessage(content="caller passes zero", tool_call_id="read", name="bash_tool"),
    ])
    fixed = list(agent.conversation.system)
    retained = list(agent.conversation.items[-2:])
    agent.native_checkpoint = AsyncMock(return_value=None)

    async def hook(*, name, input, ctx):
        assert input["task_is_retained"] is True
        return await module.CompactHook().handle(HookContext(id="fixture", name=name, input=input))

    monkeypatch.setattr("agentevolver.hook.server.hook_manager", hook)
    moved, _ = await agent._fold("test")
    assert moved and agent.conversation.complete
    assert agent.conversation.task == "Preserve the public parser API."
    assert agent.conversation.system == fixed and agent.conversation.items == retained
    assert "parse(offset=0)" in agent.conversation.checkpoint.text
    model.assert_awaited_once()
    prompt = model.await_args.kwargs["input"]["messages"][-1].text
    assert "Preserve the public parser API." in prompt
    assert "do not repeat their requirements" in prompt
    assert "argument/return" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("success,message", [(False, "provider unavailable"), (True, "  ")])
async def test_failed_or_empty_summary_keeps_original_history(monkeypatch, success, message):
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.hook.default import compact as module
    from agentevolver.message.types import AssistantMessage

    model = AsyncMock(return_value=SimpleNamespace(success=success, message=message, usage=None))
    monkeypatch.setattr(module, "model_manager", model)
    agent = Agent(retain_recent_steps=1)
    agent.conversation.extend([AssistantMessage(content="original"), AssistantMessage(content="recent")])
    original = list(agent.conversation.items)
    agent.native_checkpoint = AsyncMock(return_value=None)
    # Exercise the actual hook, without registry setup or external requests.
    async def hook(*, name, input, ctx):
        from agentevolver.hook.types import HookContext
        return await module.CompactHook().handle(HookContext(id="test", name=name, input=input))
    monkeypatch.setattr("agentevolver.hook.server.hook_manager", hook)
    moved, _ = await agent._fold("test")
    assert not moved and agent.conversation.items == original
    assert agent.conversation.checkpoint is None
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_scheduled_fold_waits_before_retry_and_resets_after_success(monkeypatch):
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.message.types import AssistantMessage

    monkeypatch.setattr("agentevolver.model.model_manager", SimpleNamespace(get_model_config=lambda _: None))
    agent = Agent(retain_recent_steps=4, compact_input_tokens=1,
                  compact_after_steps=0, compact_body_tokens=0, fold_at_pressure=0)
    for i in range(8):
        agent.conversation.note(f"Continue {i}")
        agent.conversation.append(AssistantMessage(content=f"Original evidence {i}"))
    agent.native_checkpoint = AsyncMock(return_value=None)
    agent.text_checkpoint = AsyncMock(side_effect=["", "Completed checkpoint"])
    monkeypatch.setattr(agent._events, "emit", AsyncMock())
    agent.step = 10
    await agent._fold_if_needed(())
    assert agent.text_checkpoint.await_count == 1
    assert agent._compact_retry_step == 14
    assert agent.conversation.turns == 8
    for step in (11, 12, 13):
        agent.step = step
        await agent._fold_if_needed(())
    assert agent.text_checkpoint.await_count == 1
    agent.step = 14
    await agent._fold_if_needed(())
    assert agent.text_checkpoint.await_count == 2
    assert agent._compact_retry_step == 0
    assert agent.conversation.turns == 4 and agent.conversation.complete
    assert "Completed checkpoint" in agent.conversation.checkpoint.text


@pytest.mark.asyncio
async def test_provider_overflow_can_recover_during_scheduled_retry_wait(monkeypatch):
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.message.types import AssistantMessage

    agent = Agent(retain_recent_steps=1)
    agent.conversation.extend([AssistantMessage(content="old"), AssistantMessage(content="recent")])
    agent.step = 2
    agent._compact_retry_step = 10
    agent.native_checkpoint = AsyncMock(return_value=None)
    agent.text_checkpoint = AsyncMock(return_value="Checkpoint")
    monkeypatch.setattr(agent._events, "emit", AsyncMock())
    assert await agent.make_room(trigger="overflow")
    agent.text_checkpoint.assert_awaited_once()
    assert agent._compact_retry_step == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("folded", [True, False])
async def test_trace_records_compaction_outcome_and_failure_reason(monkeypatch, folded):
    from agentevolver.hook.default.trace import TraceHook
    from agentevolver.hook.types import HookContext, HookEvent
    from agentevolver.trace import trace_manager

    emit = AsyncMock()
    monkeypatch.setattr(trace_manager, "emit", emit)
    await TraceHook().handle(HookContext(id="session", name="trace", input={
        "event": HookEvent.POST_COMPACT, "agent_name": "builder", "step": 4,
        "folded": folded, "detail": "text" if folded else "no portable checkpoint was produced",
        "tokens_before": 9000, "tokens_after": 2000 if folded else 9000,
        "retry_step": 0 if folded else 8,
    }))
    emit.assert_awaited_once()
    event = emit.await_args.args[0]
    assert event.success is folded and event.step_number == 4
    assert event.metadata["type"] == "context_compaction"
    assert event.metadata["folded"] is folded
    assert event.metadata["tokens_after"] == (2000 if folded else 9000)
    assert event.metadata["retry_step"] == (0 if folded else 8)
    if not folded:
        assert "no portable checkpoint" in event.metadata["detail"]


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
