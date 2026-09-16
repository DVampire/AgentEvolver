"""Current plans and external feedback remain usable without repeated live dumps."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.context.assembler import ContextAssembler
from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.loop.agent import Agent
from agentevolver.agent.loop.decision import ActionCall, Decision
from agentevolver.message import HumanMessage, ToolMessage
from agentevolver.runtime.envelopes import ReplyEnvelope


def turn(conversation, index):
    call = ActionCall(id=f"c{index}", name="read_file_tool", args={"path": "file"})
    conversation.add_turn(Decision(calls=[call]).as_assistant(), [
        ToolMessage(content="verified fixture", tool_call_id=call.id, name=call.name),
    ])


def test_changed_plan_survives_fold_resume_and_is_not_repeated(tmp_path):
    c = Conversation(task="Deliver website")
    c.observe("plan", "Initial plan: build undo and verify reload")
    turn(c, 1)
    latest = "Revised plan: undo AND redo; E1: inspect reusable validator; acceptance: reload"
    c.observe("plan", latest)
    for i in range(2, 8):
        turn(c, i)
        c.observe("plan", latest)
    assert sum(m.text == latest for m in c.items) == 1
    assert c.fold("Historical progress", keep_turns=2)
    assert sum(m.text == latest for m in c.items) == 1
    assert all("Initial plan" not in m.text for m in c.items)
    ContextAssembler().build(c)
    path = tmp_path / "thread.json"
    c.save(path, model="test", agent="builder")
    restored = Conversation.load(path, model="test", agent="builder")
    restored.observe("plan", latest)
    assert len(restored.items) == len(c.items)
    assert sum(m.text == latest for m in restored.items) == 1
    restored.observe("plan", "")
    assert "no longer active" in restored.items[-1].text


@pytest.mark.asyncio
async def test_feedback_is_durable_before_model_success_and_deduplicated_after_resume(tmp_path):
    agent = Agent()
    agent.ctx = SimpleNamespace(id="feedback", extra={})
    agent.conversation = Conversation(task="Deliver website")
    event = ReplyEnvelope(id="feedback-1", text="Undo must survive reload; check empty state.")
    await agent.on_event(event, None)
    # There has been no model call, and no update to any plan.
    for _ in range(2):
        assert event.text in "\n".join(m.text for m in agent.assembler.build(agent.conversation))
    path = tmp_path / "thread.json"
    agent.conversation.save(path, model="test", agent="builder")
    agent.conversation = Conversation.load(path, model="test", agent="builder")
    await agent.on_event(event, None)
    assert sum(event.text in m.text for m in agent.conversation.items) == 1
    turn(agent.conversation, 1)
    turn(agent.conversation, 2)
    assert event.text in "\n".join(m.text for m in agent.conversation.foldable(1))


def test_notes_cannot_split_a_native_tool_turn():
    c = Conversation()
    c.append(Decision(calls=[ActionCall(id="pending", name="read")]).as_assistant())
    with pytest.raises(ValueError, match="closed tool turn"):
        c.note("feedback", event_id="e")
    assert len(c.items) == 1


def test_length_target_does_not_veto_summary_or_replace_current_plan():
    c = Conversation()
    c.observe("plan", "Keep exact requirements " * 500)
    for index in range(3):
        turn(c, index)
    assembler = ContextAssembler(retain_turns=1, compact_output_tokens=256)
    summary = "Repeated summary " * 200
    plan = c.observations[0]
    assert assembler.fold(c, summary)
    assert summary.strip() in c.checkpoint.text
    assert sum(item is plan for item in c.items) == 1
    assert c.turns == 1 and c.complete


@pytest.mark.asyncio
async def test_long_checkpoint_is_applied_after_one_summary_call(tmp_path):
    agent = Agent(retain_recent_steps=1, compact_output_tokens=256)
    agent._thread_path = tmp_path / "thread.json"
    agent.conversation.task = "Keep existing public URLs"
    for i in range(3):
        turn(agent.conversation, i)
    agent.native_checkpoint = AsyncMock(return_value=None)
    agent.text_checkpoint = AsyncMock(return_value="too verbose " * 2000)
    moved, reason = await agent._fold("test")
    assert moved and reason == "text"
    agent.text_checkpoint.assert_awaited_once()
    assert "too verbose " * 2000 in agent.conversation.checkpoint.text
    assert agent.conversation.task == "Keep existing public URLs"
    assert agent.conversation.turns == 1 and agent.conversation.complete
    archives = list(tmp_path.glob("thread/archive/*.json"))
    assert len(archives) == 1 and str(archives[0]) in agent.conversation.checkpoint.text
