"""Regressions for repeated folds: complete requests, bounded tails and backoff."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from agentevolver.agent.loop.agent import Agent
from agentevolver.message import AssistantMessage, HumanMessage, SystemMessage, ToolCall, ToolMessage


def turn(agent, index, size=20):
    agent.conversation.add_turn(AssistantMessage(content="inspect", tool_calls=[ToolCall(
        id=str(index), function={"name": "read", "arguments": "{}"},
    )]), [ToolMessage(content="x" * size, tool_call_id=str(index), name="read")])


@pytest.mark.asyncio
async def test_native_growth_uses_existing_portable_summary_and_shrinks_full_request(monkeypatch):
    agent = Agent(retain_recent_steps=4, compact_input_tokens=2000, compact_output_tokens=256)
    agent.conversation.task = "Keep the exact acceptance contract"
    for i in range(5):
        turn(agent, i, 10_000 if i < 4 else 20)
    native = {"summary": "Observed results; next verify saves", "provider_state": {
        "responses": {"compaction_items": [{"type": "compaction", "encrypted_content": "x" * 100_000}]},
    }}
    agent.native_checkpoint = AsyncMock(return_value=native)
    agent.text_checkpoint = AsyncMock()
    events = AsyncMock()
    monkeypatch.setattr(agent._events, "emit", events)
    assert await agent.make_room(trigger="full input")
    final = events.await_args.args[1]
    assert final["full_input_after"] < final["full_input_before"]
    assert final["full_input_after"] <= final["headroom_target"] == 1500
    assert final["headroom_reached"] and "native checkpoint exceeded" in final["detail"]
    assert not agent.conversation.checkpoint.provider_state
    assert agent.conversation.turns == 1 and agent.conversation.complete
    assert "Observed results" in agent.conversation.checkpoint.text
    assert agent.conversation.task == "Keep the exact acceptance contract"
    source = agent.native_checkpoint.await_args.args[0]
    assert len([m for m in source if isinstance(m, ToolMessage)]) == 4
    agent.text_checkpoint.assert_not_awaited()
    assert agent._unproductive_folds == 0 and agent._compact_retry_step == 0


@pytest.mark.asyncio
async def test_large_early_tool_result_can_fold_before_four_turns_accumulate(monkeypatch):
    agent = Agent(retain_recent_steps=4, compact_input_tokens=2000, compact_output_tokens=256)
    turn(agent, 1, 20_000)
    turn(agent, 2)
    agent.native_checkpoint = AsyncMock(return_value=None)
    agent.text_checkpoint = AsyncMock(return_value="Large file inspected; verify the implementation")
    monkeypatch.setattr(agent._events, "emit", AsyncMock())
    await agent._fold_if_needed(())
    agent.text_checkpoint.assert_awaited_once()
    assert agent.conversation.turns == 1 and agent.conversation.complete


@pytest.mark.asyncio
async def test_fixed_floor_waits_for_both_steps_and_growth_without_repeated_summary_calls(monkeypatch):
    agent = Agent(retain_recent_steps=1, compact_input_tokens=1000, compact_output_tokens=256)
    agent.conversation.system = [SystemMessage(content="fixed requirement " * 2000)]
    for i in range(4):
        turn(agent, i, 1000)
    agent.native_checkpoint = AsyncMock(return_value=None)
    agent.text_checkpoint = AsyncMock(return_value="Facts and next action")
    monkeypatch.setattr(agent._events, "emit", AsyncMock())
    agent.step = 10
    await agent._fold_if_needed(())
    assert agent.text_checkpoint.await_count == 1
    assert agent._compact_retry_step > 10 and agent._compact_rearm_tokens > 1000
    turn(agent, 11)
    for step in range(11, 31):
        agent.step = step
        await agent._fold_if_needed(())
    # A static prompt above threshold must not force a paid fold at every step.
    assert agent.text_checkpoint.await_count == 1
    turn(agent, 31, 10_000)
    agent.step = 31
    await agent._fold_if_needed(())
    assert agent.text_checkpoint.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_live_image_counts_toward_trigger_but_never_enters_checkpoint(monkeypatch, native):
    agent = Agent(retain_recent_steps=1, compact_input_tokens=3000, compact_output_tokens=256)
    for i in range(4):
        turn(agent, i, 1000)
    # An unknown-size image has a conservative 4096-token visual budget. Its bytes
    # stay outside history, even when that irreducible live floor triggers a fold.
    agent._environment_observations = {"visual": {"extra": {"screenshots": [
        {"screenshot": "live-frame", "screenshot_description": "Current game"},
    ]}}}
    agent.native_checkpoint = AsyncMock(return_value=(
        {"summary": "Facts and next action", "provider_state": {
            "responses": {"compaction_items": [{"type": "compaction", "encrypted_content": "small"}]},
        }} if native else None
    ))
    agent.text_checkpoint = AsyncMock(return_value="Facts and next action")
    events = AsyncMock()
    monkeypatch.setattr(agent._events, "emit", events)
    assert agent.assembler.estimate(agent.conversation) < 3000
    agent.step = 10
    await agent._fold_if_needed(())
    agent.native_checkpoint.assert_awaited_once()
    for call in [*agent.native_checkpoint.await_args_list, *agent.text_checkpoint.await_args_list]:
        assert all(isinstance(m, (AssistantMessage, ToolMessage)) for m in call.args[0])
        assert "live-frame" not in str(call.args[0])
    assert agent.conversation.checkpoint is not None
    assert "live-frame" not in agent.conversation.checkpoint.text
    final = events.await_args.args[1]
    assert final["full_input_before"] > 4096 and final["full_input_after"] > 4096
    assert final["productive"] and not final["headroom_reached"]
    # Refreshing same-size frames must not repeatedly re-arm compaction.
    for step in range(11, 31):
        agent.step = step
        agent._environment_observations["visual"]["extra"]["screenshots"][0]["screenshot"] = f"frame-{step}"
        await agent._fold_if_needed(())
    agent.native_checkpoint.assert_awaited_once()
    assert agent.text_checkpoint.await_count == (0 if native else 1)
    assert "frame-30" in str(agent.attachments())


@pytest.mark.asyncio
async def test_capacity_bypasses_scheduled_backoff(monkeypatch):
    agent = Agent(retain_recent_steps=1)
    for i in range(3):
        turn(agent, i)
    agent._compact_retry_step = 100
    agent._compact_rearm_tokens = 1_000_000
    agent._measure_compaction_context = AsyncMock(return_value={
        "estimated_tokens_after": 950_000, "pressure_ratio_after": 0.95,
    })
    agent.make_room = AsyncMock(return_value=True)
    await agent._fold_if_needed(())
    agent.make_room.assert_awaited_once()
    assert "capacity" in agent.make_room.await_args.kwargs["trigger"]


@pytest.mark.asyncio
async def test_a_larger_text_checkpoint_is_rejected_without_losing_exact_history(monkeypatch):
    agent = Agent(retain_recent_steps=1)
    for i in range(4):
        turn(agent, i)
    original = list(agent.conversation.items)
    agent.native_checkpoint = AsyncMock(return_value=None)
    agent.text_checkpoint = AsyncMock(return_value="Too much summary " * 10_000)
    events = AsyncMock()
    monkeypatch.setattr(agent._events, "emit", events)
    assert not await agent.make_room(trigger="test")
    assert agent.conversation.items == original and agent.conversation.checkpoint is None
    final = events.await_args.args[1]
    assert not final["productive"]
    assert final["candidate_full_input_after"] > final["full_input_before"]
    assert agent._unproductive_folds == 1 and agent._compact_rearm_tokens > 0


@pytest.mark.asyncio
async def test_cancellation_during_summary_keeps_history_and_archive(monkeypatch, tmp_path):
    agent = Agent(retain_recent_steps=1)
    agent._thread_path = tmp_path / "thread.json"
    for i in range(3):
        turn(agent, i)
    original = list(agent.conversation.items)
    agent.native_checkpoint = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(agent._events, "emit", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await agent.make_room(trigger="test")
    assert agent.conversation.items == original and agent.conversation.checkpoint is None
    assert len(list(tmp_path.glob("thread/archive/*.json"))) == 1


@pytest.mark.asyncio
async def test_native_programs_use_portable_route_without_a_known_bad_request():
    from agentevolver.model.llm_hub.response import ResponseLLMHub

    client = ResponseLLMHub(model="test")
    compact = AsyncMock()
    client._client = lambda: SimpleNamespace(responses=SimpleNamespace(compact=compact))
    message = AssistantMessage(content="", provider_state={"responses": {"output_items": [
        {"type": "program", "code": "text('observed')"},
        {"type": "program_output", "output": "observed"},
    ]}})
    assert not client.compaction_ready([message])
    assert await client.compact_history([message]) is None
    compact.assert_not_awaited()
