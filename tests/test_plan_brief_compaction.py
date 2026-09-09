"""Plan projection, history boundaries and old-thread migration without model calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.context.assembler import ContextAssembler
from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.loop.agent import Agent
from agentevolver.message import AssistantMessage, CompactionMessage, HumanMessage, ToolCall, ToolMessage
from agentevolver.plan.server import PLAN_BRIEF_MAX_CHARS, plan_brief


def add_turn(c, index, body="result"):
    c.add_turn(AssistantMessage(content="inspect", tool_calls=[ToolCall(
        id=str(index), function={"name": "read", "arguments": "{}"},
    )]), [ToolMessage(content=body, tool_call_id=str(index), name="read")])


def test_brief_projects_only_authored_status_with_nested_headings_and_fenced_examples():
    text = """# Project
```markdown
## Brief
Example status, not the real Brief.
```
## Brief
- W1 implemented; verification pending → Implementation
### Next
Check the saved game.
## Implementation
FULL DESIGN MUST STAY ON DISK
"""
    brief = plan_brief(text)
    assert "W1 implemented" in brief and "Check the saved game" in brief
    assert "Example status" not in brief and "FULL DESIGN" not in brief
    assert len(plan_brief("## Brief\n" + "状态" * 5000)) <= PLAN_BRIEF_MAX_CHARS
    fallback = plan_brief("# Project\n## Implementation\nSecret design.\n## Verification\nPending.")
    assert "L2: Implementation" in fallback and "Progress is not inferred" in fallback
    assert "Secret design" not in fallback and "Pending." not in fallback


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_live_brief_never_reaches_either_compactor_but_feedback_and_tools_do(monkeypatch, native):
    agent = Agent(retain_recent_steps=1, use_plan=True, compact_strategy="native")
    agent.ctx = SimpleNamespace(id="test", extra={})
    monkeypatch.setattr("agentevolver.plan.server.plan_manager.context",
                        lambda *a, **k: '<plan-context path="/plan.md"><plan-brief>W1 DONE</plan-brief></plan-context>')
    monkeypatch.setattr(agent, "environment_state", AsyncMock(return_value=""))
    agent.conversation.task = "FIXED TASK"
    agent.conversation.note("New user requirement: preserve save compatibility")
    for i in range(5):
        blocks = await agent._live_blocks(i)
        assert "W1 DONE" in "\n".join(blocks)
        add_turn(agent.conversation, i, "Explicit file read: plan says verify saves")
    native_result = {"summary": "Save compatibility must be verified", "format": "fixture",
                     "provider_state": {"responses": {"compaction_items": [{"type": "compaction"}]}}}
    compact = AsyncMock(return_value=native_result if native else None)
    monkeypatch.setattr("agentevolver.model.model_manager", SimpleNamespace(compact_history=compact))
    hook = AsyncMock(return_value=SimpleNamespace(output="Verify save compatibility", usage=None))
    monkeypatch.setattr("agentevolver.hook.server.hook_manager", hook)
    assert (await agent._fold("test"))[0]
    source = "\n".join(m.text for m in compact.await_args.args[1])
    assert "New user requirement" in source and "Explicit file read" in source
    assert "W1 DONE" not in source and "FIXED TASK" not in source
    if not native:
        assert all("plan-context" not in record for record in hook.await_args.kwargs["input"]["items"])
    else:
        hook.assert_not_awaited()
    assert agent.conversation.complete and agent.conversation.turns == 1
    envelope = agent.assembler.build_envelope(agent.conversation, live=await agent._live_blocks(6))
    assert "W1 DONE" in "\n".join(m.text for m in envelope.live)
    assert all("plan-context" not in m.text for m in envelope.recent + envelope.checkpoint)


def test_resume_removes_old_automatic_plans_but_preserves_feedback_tool_reads_and_native_state(tmp_path):
    c = Conversation(task="Task")
    old = '<plan-context path="/plan.md"><current-plan>OLD PLAN</current-plan></plan-context>'
    c.observe("plan", old)
    c.note("User asks for new acceptance criteria")
    add_turn(c, 1, old)  # An actual tool result must not be deleted.
    c.append(AssistantMessage(content="Will check"))
    c.observe("plan", old.replace("OLD PLAN", "NEW PLAN"))
    add_turn(c, 2)
    native_plan = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": old}]}
    user = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Keep this user constraint"}]}
    opaque = {"type": "compaction", "encrypted_content": "opaque-unchanged"}
    c.checkpoint = CompactionMessage(content="Readable facts", provider_state={
        "responses": {"compaction_items": [native_plan, user, opaque]},
    })
    path = tmp_path / "thread.json"
    c.save(path, model="test", agent="builder")
    original = path.read_bytes()
    restored = Conversation.load(path, model="test", agent="builder")
    assert path.read_bytes() == original
    assert not restored.observations
    assert all("<plan-context" not in m.text for m in restored.items if isinstance(m, HumanMessage))
    assert any(m.text == old for m in restored.items if isinstance(m, ToolMessage))
    assert any("new acceptance" in m.text for m in restored.items)
    assert restored.checkpoint.provider_state["responses"]["compaction_items"] == [user, opaque]
    ContextAssembler().build(restored)  # No orphan calls or adjacent assistant turns.
    restored.save(tmp_path / "new.json", model="test", agent="builder")
    again = Conversation.load(tmp_path / "new.json", model="test", agent="builder")
    assert again.items == restored.items


def test_text_checkpoint_preserves_native_program_evidence_without_binary_or_reasoning():
    message = AssistantMessage(content="", provider_state={"responses": {"output_items": [
        {"type": "program", "code": "read_plan()"},
        {"type": "program_output", "output": ["verified", {"type": "input_image", "image_url": "data:binary"}]},
        {"type": "reasoning", "encrypted_content": "opaque-private"},
    ]}})
    text = Agent._render_for_checkpoint(message)
    assert "read_plan()" in text and "verified" in text and "[image]" in text
    assert "data:binary" not in text and "opaque-private" not in text
