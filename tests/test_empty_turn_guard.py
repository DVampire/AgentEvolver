"""A model that returns nothing must not be allowed to spend the whole budget.

A ProgramBench instance did exactly this: 100 steps, every one an empty reasoning and
an empty tool-call list, `step += 1` each time, ending at the step ceiling with a zero
score and nothing logged as wrong. The call was not failing — `think_failures` covers
that — and it was not a text-only turn the nudge could correct, because there was no
text. It was a successful response that said nothing, a case no guard watched.
"""

from __future__ import annotations

import asyncio
import contextlib
import io

import pytest

from agentevolver.agent.types import _EMPTY_TURNS_BEFORE_GIVING_UP


def _run_with_stubbed_turn(decision_sequence):
    """Drive a real agent's `_advance_once` loop with `_think` returning canned decisions.

    Everything a turn touches around the model call is stubbed to a no-op, so the test is
    about the loop's own accounting — how many empty turns it tolerates — and nothing
    else. Returns (steps_taken, run).
    """
    from agentevolver.agent.actor.general_agent import GeneralAgent
    from agentevolver.agent.types import _AgentRun

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/claude-1014")
    agent.max_step = 100  # far above the empty-turn ceiling, so the ceiling is what stops it

    calls = {"think": 0}

    async def fake_think(*a, **k):
        i = calls["think"]
        calls["think"] += 1
        # After the scripted decisions, keep returning the last one.
        return decision_sequence[min(i, len(decision_sequence) - 1)]

    async def noop(*a, **k):
        return None

    async def no_constraint(*a, **k):
        return None, []

    async def fake_messages(*a, **k):
        return []

    concluded = {"done": False}

    async def fake_conclude(run):
        concluded["done"] = True
        run._concluded = True

    agent._think = fake_think
    agent._get_messages = fake_messages
    agent._constraint_check = no_constraint
    agent._post_step = noop
    agent._conclude = fake_conclude

    run = _AgentRun(task="t", files=None, ctx=None, ref=None, task_id="x", extra_kwargs={})

    async def drive():
        # _advance loops until a turn concludes; guard against a real infinite loop in
        # the test by capping iterations well above any correct behaviour.
        for _ in range(500):
            cont = await agent._advance_once(run)
            if not cont:
                return
        raise AssertionError("the loop never concluded — the guard did not fire")

    asyncio.run(drive())
    return run, concluded


EMPTY = {"tool_calls": [], "routing": {}, "reasoning": "", "step_tokens": 0,
         "step_usage": None, "error": None}
TEXT_ONLY = {**EMPTY, "reasoning": "I am thinking about what to do next."}


def test_a_run_of_empty_turns_stops_before_the_budget_is_gone():
    """Neither reasoning nor a call, repeatedly, is a model that will not answer."""
    run, concluded = _run_with_stubbed_turn([EMPTY])

    assert concluded["done"], "the empty-turn guard never concluded the run"
    assert run.done is False
    assert run.step <= _EMPTY_TURNS_BEFORE_GIVING_UP + 1, (
        f"took {run.step} steps to stop on empty turns; the ceiling is "
        f"{_EMPTY_TURNS_BEFORE_GIVING_UP}")
    assert "empty" in (run.result or "").lower()


def test_text_only_turns_are_not_counted_as_empty():
    """A turn with reasoning but no call is the case the nudge handles, not this guard.

    Miscounting it would end a run that is thinking out loud — exactly the turns the
    'you produced text but called no tool' nudge exists to redirect.
    """
    from agentevolver.agent.actor.general_agent import GeneralAgent
    from agentevolver.agent.types import _AgentRun

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/claude-1014")
    agent.max_step = _EMPTY_TURNS_BEFORE_GIVING_UP + 3  # a real ceiling, past the empty one

    async def fake_think(*a, **k):
        return TEXT_ONLY

    async def noop(*a, **k):
        return None

    async def no_constraint(*a, **k):
        return None, []

    async def fake_messages(*a, **k):
        return []

    reached_max = {"hit": False}
    orig = agent._conclude

    async def fake_conclude(run):
        reached_max["hit"] = True

    agent._think = fake_think
    agent._get_messages = fake_messages
    agent._constraint_check = no_constraint
    agent._post_step = noop
    agent._conclude = fake_conclude

    run = _AgentRun(task="t", files=None, ctx=None, ref=None, task_id="x", extra_kwargs={})

    async def drive():
        for _ in range(500):
            if not await agent._advance_once(run):
                return
        raise AssertionError("loop never concluded")

    asyncio.run(drive())

    # It stops at the step ceiling, not the empty-turn ceiling: every text-only turn
    # reset the counter, so the run used its full (small) budget rather than being
    # killed early.
    assert run.step >= _EMPTY_TURNS_BEFORE_GIVING_UP + 3, (
        f"a text-only run stopped at step {run.step}, before its step ceiling — the "
        f"empty-turn guard counted turns that had reasoning")
