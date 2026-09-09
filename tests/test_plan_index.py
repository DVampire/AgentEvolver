"""The live task index locates durable records without loading their bodies."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.loop.agent import Agent
from agentevolver.message import AssistantMessage, ToolCall, ToolMessage
from agentevolver.paths import P, path_manager
from agentevolver.plan.server import PLAN_INDEX_MAX_CHARS, plan_index_path, plan_manager, read_plan_index
from agentevolver.plan.types import PlanMode


def test_index_is_the_only_live_document_and_edits_are_visible(bound_session, monkeypatch):
    root = bound_session["plan"]
    sid = bound_session["project"].name
    (root / "plan.md").write_text("## Brief\nSTALE PROGRESS\n## Design\nFULL DESIGN")
    (root / "evaluation.md").write_text("FULL EVALUATION BODY" * 1000)
    index = root / "index.md"
    index.write_text("## Brief\nW1 implemented; verify next.\n## Documents\n"
                     "[Plan](plan.md): detailed design.\n[Evaluation](evaluation.md): two checks pending.")
    def cannot_read_plan(*args, **kwargs):
        raise AssertionError("live index must not read full plan")
    monkeypatch.setattr("agentevolver.plan.server.read_plan", cannot_read_plan)
    context = plan_manager.context(sid, enabled=True, include_rules=False)
    assert "W1 implemented" in context and "two checks pending" in context
    assert str(index) in context and str(root) in context
    assert "FULL DESIGN" not in context and "STALE PROGRESS" not in context
    assert "FULL EVALUATION BODY" not in context
    index.write_text(index.read_text().replace("two checks pending", "two checks passed on build A"))
    assert "two checks passed on build A" in plan_manager.context(sid, enabled=True)
    plan_manager.set_mode(sid, PlanMode.OFF)
    try:
        assert plan_manager.context(sid, enabled=True) == ""
    finally:
        plan_manager.forget(sid)


def test_old_plan_brief_is_a_read_only_fallback_until_index_exists(bound_session):
    plan = bound_session["plan"] / "plan.md"
    plan.write_text("## Brief\nExisting progress, not forgotten.\n## Detail\nPRIVATE DETAIL")
    index = plan_index_path()
    text = read_plan_index()
    assert "Existing progress" in text and "index.md is missing" in text
    assert "PRIVATE DETAIL" not in text and not index.exists()
    index.write_text("## Brief\nNew progress")
    assert read_plan_index() == "## Brief\nNew progress"
    assert "Existing progress" in plan.read_text()


def test_index_budget_is_shared_by_brief_and_documents_and_does_not_cut_links(bound_session):
    index = plan_index_path()
    index.write_text("## Brief\nCheck next.\n## Documents\n" + "[Long document](" + "x" * 6000 + ")")
    text = read_plan_index()
    assert len(text) <= PLAN_INDEX_MAX_CHARS
    assert "Check next" in text and "Index truncated" in text
    assert "[Long document]" not in text
    index.write_bytes(b"\xff")
    assert "unreadable" in read_plan_index()


def test_index_follows_authoritative_plan_path_override(bound_session, tmp_path):
    custom = tmp_path / "navigation" / "plan.md"
    path_manager.override(P.SESSION_PLAN, custom)
    assert plan_index_path() == custom.with_name("index.md")


@pytest.mark.asyncio
async def test_actual_index_stays_live_across_text_compaction(bound_session, monkeypatch):
    index = plan_index_path()
    index.write_text("## Brief\nLIVE INDEX STATUS\n## Documents\n[Evaluation](evaluation.md): pending")
    agent = Agent(use_plan=True, retain_recent_steps=1)
    agent.ctx = SimpleNamespace(id=bound_session["project"].name, extra={})
    monkeypatch.setattr(agent, "environment_state", AsyncMock(return_value=""))
    monkeypatch.setattr(agent, "on_step", AsyncMock(return_value=""))
    for i in range(3):
        agent.conversation.add_turn(AssistantMessage(content="Check", tool_calls=[ToolCall(
            id=str(i), function={"name": "read", "arguments": "{}"},
        )]), [ToolMessage(content="Actual evaluation file read", tool_call_id=str(i), name="read")])
    agent.text_checkpoint = AsyncMock(return_value="Observed check; next verify")
    assert "LIVE INDEX STATUS" in "\n".join(await agent._live_blocks(0))
    assert (await agent._fold("test"))[0]
    source = agent.text_checkpoint.await_args.args[0]
    assert all("LIVE INDEX STATUS" not in m.text for m in source)
    assert any("Actual evaluation file read" in m.text for m in source)
    index.write_text("## Brief\nUPDATED INDEX STATUS")
    assert "UPDATED INDEX STATUS" in "\n".join(await agent._live_blocks(1))
