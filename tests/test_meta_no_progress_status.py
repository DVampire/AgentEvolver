"""A no-progress stop must be archived as failure, not as completed work."""

from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_meta_no_progress_conclusion_keeps_done_false(monkeypatch):
    from agentevolver.agent.actor.meta_agent import MetaAgent
    from agentevolver.agent.types import Agent

    with contextlib.redirect_stdout(io.StringIO()):
        agent = MetaAgent(base_dir="/tmp/agent-evolver-meta-guard-test")

    call = SimpleNamespace(name="write_file_tool", input={"path": "/tmp/a"})
    decision = {
        "tool_calls": [call], "reasoning": "retry", "assistant_text": "",
        "provider_state": {}, "step_tokens": 1, "step_usage": None,
    }
    run = SimpleNamespace(
        previous_action_signature=None, repeated_action_rounds=0,
        round_step=0, step=0, task_id="t", ctx=None, messages=[],
        action_errors=[], retry_now=False, done=False, result=None, reasoning=None,
    )

    async def pass_through(self, current_run, current_decision):
        return current_decision["tool_calls"]

    async def noop(*args, **kwargs):
        return None

    concluded = {"value": False}

    async def conclude(current_run):
        concluded["value"] = True

    monkeypatch.setattr(Agent, "_prepare_round", pass_through)
    agent._post_step = noop
    agent._conclude = conclude

    assert await agent._prepare_round(run, decision) == [call]  # first proposal
    assert await agent._prepare_round(run, decision) is None    # corrective retry
    assert run.retry_now is True
    run.retry_now = False
    assert await agent._prepare_round(run, decision) is None    # terminal guard

    assert concluded["value"] is True
    assert run.done is False
    assert "incomplete" in run.result
