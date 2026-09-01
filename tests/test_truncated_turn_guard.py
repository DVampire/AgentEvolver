"""A max-token response is not a complete assistant/tool protocol turn."""

from __future__ import annotations

import asyncio
import contextlib
import io

from agentevolver.agent.types import _TRUNCATED_TURNS_BEFORE_GIVING_UP


TRUNCATED = {
    "tool_calls": [],
    "routing": {},
    "reasoning": "partial private reasoning",
    "assistant_text": "",
    "provider_state": {"anthropic": {"thinking_blocks": [{"type": "thinking"}]}},
    "step_tokens": 32_768,
    "step_usage": {"output_tokens": 32_768},
    "error": None,
    "overflowed": False,
    "truncated": True,
}


def _drive_truncated_turns():
    from agentevolver.agent.actor.general_agent import GeneralAgent
    from agentevolver.agent.types import _AgentRun

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/agent-evolver-truncated-turn-test")
    agent.max_step = 100

    async def fake_think(*args, **kwargs):
        return dict(TRUNCATED)

    async def fake_messages(*args, **kwargs):
        return []

    async def no_constraint(*args, **kwargs):
        return None, []

    recorded = []

    async def record_step(*args, **kwargs):
        recorded.append(kwargs)

    async def fake_conclude(run):
        run._concluded = True

    agent._think = fake_think
    agent._get_messages = fake_messages
    agent._constraint_check = no_constraint
    agent._post_step = record_step
    agent._conclude = fake_conclude

    run = _AgentRun(task="t", files=None, ctx=None, ref=None, task_id="x", extra_kwargs={})

    async def drive():
        for _ in range(20):
            if not await agent._advance_once(run):
                return
        raise AssertionError("truncated-turn guard never concluded")

    asyncio.run(drive())
    return run, recorded


def test_repeated_max_token_turns_fail_without_replaying_partial_protocol_state():
    run, recorded = _drive_truncated_turns()

    assert run.done is False
    assert run.truncated_turns == _TRUNCATED_TURNS_BEFORE_GIVING_UP
    assert "output-token limit" in (run.result or "")
    assert len(recorded) == _TRUNCATED_TURNS_BEFORE_GIVING_UP
    assert all(item["provider_state"] == {} for item in recorded)
    assert all(item["plan"] == [] for item in recorded)


def test_first_max_token_turn_requests_a_smaller_chunk():
    run, _ = _drive_truncated_turns()

    # The final attempt preserves the correction from the preceding retry.
    correction = "\n".join(run.action_errors)
    assert "partial tool call was discarded" in correction
    assert "smaller action" in correction
