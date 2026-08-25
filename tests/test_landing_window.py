"""`max_step` is a hard force-stop: the turn that reaches it is abandoned mid-action and
the run is marked "not completed". A run still writing when it ran out ships only what it
last persisted — which is how a stopped ProgramBench run lost its uncommitted refinements.

The landing window reserves the last few steps of a bounded run and, while in it, hangs an
unmissable "persist and finish now" directive on the prompt (on top of the softer budget
tiers) so the agent lands its work before the hard stop rather than being cut off. These
tests pin the window arithmetic and the directive injection.
"""

from __future__ import annotations

import asyncio
import contextlib
import io

from agentevolver.agent.types import _in_landing_window, _LANDING_RESERVE_STEPS


class TestLandingWindow:
    def test_a_normal_bounded_run_is_not_landing_until_the_reserve(self):
        # max_step 50, reserve 3 -> landing only at step 47, 48, 49 (0-based; step+1 taken).
        assert not _in_landing_window(0, 50)
        assert not _in_landing_window(46, 50)
        assert _in_landing_window(47, 50)
        assert _in_landing_window(49, 50)

    def test_a_large_budget_reserves_the_same_small_tail(self):
        # The reserve is a fixed handful of steps, not a fraction — a 400-step coordinator
        # lands in its last few steps, not its last quarter.
        assert not _in_landing_window(396, 400)
        assert _in_landing_window(397, 400)
        assert _in_landing_window(399, 400)

    def test_a_tiny_budget_does_not_become_all_landing(self):
        # max_step 4 -> reserve scaled to 1, so only the final step lands; the run still
        # gets most of its budget to work before the landing nudge.
        assert not _in_landing_window(0, 4)
        assert not _in_landing_window(2, 4)
        assert _in_landing_window(3, 4)

    def test_an_effectively_unbounded_budget_never_lands(self):
        # max_step <= 0 is stored as 1e8; there is no end to land before, so the directive
        # must never fire (it would nag every step of an open-ended run).
        assert not _in_landing_window(0, int(1e8))
        assert not _in_landing_window(10_000_000, int(1e8))

    def test_the_reserve_never_exceeds_the_configured_handful(self):
        # However large the budget, the window is at most _LANDING_RESERVE_STEPS wide.
        max_step = 1000
        landing_steps = [s for s in range(max_step) if _in_landing_window(s, max_step)]
        assert len(landing_steps) == _LANDING_RESERVE_STEPS
        assert landing_steps == list(range(max_step - _LANDING_RESERVE_STEPS, max_step))


def test_advance_once_flips_run_landing_inside_the_window():
    """The loop must set run.landing before it builds the prompt, so the directive rides
    the turns in the window and not before. Drive the real `_advance_once` with everything
    around the model call stubbed, and record run.landing as seen when the prompt is built."""
    from agentevolver.agent.actor.general_agent import GeneralAgent
    from agentevolver.agent.types import _AgentRun

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/claude-1014")
    agent.max_step = 10  # reserve 2 -> landing at steps 8 and 9

    seen = {}  # step -> run.landing when the prompt was built

    async def fake_think(*a, **k):
        return {"tool_calls": [], "routing": {}, "reasoning": "keep going",
                "step_tokens": 0, "step_usage": None, "error": None}

    async def fake_messages(*a, **k):
        run = k.get("_run")
        if run is not None:
            seen[run.step] = run.landing
        return []

    async def noop(*a, **k):
        return None

    async def no_constraint(*a, **k):
        return None, []

    async def fake_conclude(run):
        run._concluded = True

    agent._think = fake_think
    agent._get_messages = fake_messages
    agent._constraint_check = no_constraint
    agent._fold_ahead = noop
    agent._post_step = noop
    agent._conclude = fake_conclude

    run = _AgentRun(task="t", files=None, ctx=None, ref=None, task_id="x", extra_kwargs={})

    async def drive():
        for _ in range(50):
            if not await agent._advance_once(run):
                return
        raise AssertionError("loop never concluded")

    asyncio.run(drive())

    # Steps 0..7 build the prompt outside the window; 8 and 9 inside it.
    assert seen.get(0) is False and seen.get(7) is False
    assert seen.get(8) is True and seen.get(9) is True


def test_the_landing_directive_is_injected_only_when_landing():
    """`_get_agent_context` must append the persist-and-finish directive exactly when the
    run is landing — the text that turns the soft budget tier into a hard last-chance."""
    from agentevolver.agent.actor.general_agent import GeneralAgent
    from agentevolver.agent.types import _AgentRun

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/claude-1014")

    def context_for(landing: bool) -> str:
        run = _AgentRun(task="t", files=None, ctx=None, ref=None, task_id="x", extra_kwargs={})
        run.landing = landing
        modules = asyncio.run(agent._get_agent_context("t", ctx=None, _run=run))
        return "".join(str(v) for v in modules.values())

    off = context_for(False)
    on = context_for(True)
    assert "LANDING WINDOW" not in off
    assert "LANDING WINDOW" in on
    assert "persist" in on.lower()
