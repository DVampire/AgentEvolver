import pytest
from types import SimpleNamespace

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.hook import hook_manager
from agentevolver.hook.default.no_progress import NoProgressHook, progress_policy
from agentevolver.hook.types import HookContext, HookDecision, HookEvent


class _GuardAgent(Agent):
    name: str = "guard_agent"
    description: str = "test"
    metadata: dict = {}

    async def _post_step(self, *args, **kwargs):
        self.posted = getattr(self, "posted", 0) + 1

    async def _conclude(self, run):
        self.concluded = getattr(self, "concluded", 0) + 1


def _context(actions, evidence=None, fingerprint="same"):
    return HookContext(
        id="run-1",
        name="no_progress_hook",
        workspace_root="/tmp/workspace",
        input={
            "event": HookEvent.PRE_ACTION,
            "actions": actions,
            "evidence": evidence or {},
            "workspace_fingerprint": fingerprint,
        },
    )


@pytest.mark.asyncio
async def test_blocks_unchanged_successful_workspace_action():
    hook = NoProgressHook()
    action = {"name": "read_file_tool", "kind": "tool", "signature": "read", "policy": "workspace"}
    result = await hook.handle(_context(
        [action],
        {"read": {"success": True, "workspace_fingerprint": "same"}},
    ))
    assert result.decision == HookDecision.BLOCK
    assert "read_file_tool" in result.reason


@pytest.mark.asyncio
async def test_allows_repeat_after_workspace_changes():
    hook = NoProgressHook()
    action = {"name": "bash_tool", "kind": "tool", "signature": "test", "policy": "workspace"}
    result = await hook.handle(_context(
        [action],
        {"test": {"success": True, "workspace_fingerprint": "before"}},
        fingerprint="after",
    ))
    assert result.decision == HookDecision.ALLOW


@pytest.mark.asyncio
async def test_external_polling_and_mutating_actions_are_not_blocked():
    hook = NoProgressHook()
    actions = [
        {"name": "web_searcher_tool", "kind": "tool", "signature": "search"},
        {"name": "wait_tool", "kind": "tool", "signature": "wait"},
        {"name": "write_file_tool", "kind": "tool", "signature": "write"},
    ]
    evidence = {
        action["signature"]: {"success": True, "workspace_fingerprint": "same"}
        for action in actions
    }
    result = await hook.handle(_context(actions, evidence))
    assert result.decision == HookDecision.ALLOW
    assert [progress_policy(action) for action in actions] == ["external", "polling", "always"]


@pytest.mark.asyncio
async def test_base_agent_skips_repeat_and_feeds_correction(tmp_path):
    await hook_manager.initialize(hook_names=["no_progress_hook"])
    agent = _GuardAgent(base_dir=str(tmp_path), use_memory=False)
    ctx = AgentContext(id="ctx", workspace_root=str(tmp_path))
    call = SimpleNamespace(name="read_file_tool", input={"path": str(tmp_path / "a.py")})
    signature = agent._action_signature("tool", call.name, call.input)
    run = SimpleNamespace(
        task_id="task", ctx=ctx, action_evidence={
            signature: {
                "success": True,
                "workspace_fingerprint": await agent._workspace_fingerprint(ctx),
            }
        },
        no_progress_rounds=0, step=1, messages=[], action_errors=[],
        done=False, result=None, reasoning=None,
        produced_change=True, baseline_fingerprint=None,
    )
    decision = {
        "tool_calls": [call],
        "routing": {call.name: ("tool", call.name)},
        "reasoning": "repeat",
        "step_tokens": 1,
    }

    assert await agent._prepare_round(run, decision) is None
    assert run.step == 2
    assert run.no_progress_rounds == 1
    assert "No-progress guard" in run.action_errors[0]
    assert agent.posted == 1
    # The guard asks for another turn by setting a flag, which `_advance`'s loop reads.
    # It used to call `_advance` itself: one nested frame per blocked proposal, against
    # Python's recursion limit, in a loop whose whole purpose is to repeat.
    assert run.retry_now is True


@pytest.mark.asyncio
async def test_base_agent_stops_third_no_progress_proposal(tmp_path):
    await hook_manager.initialize(hook_names=["no_progress_hook"])
    agent = _GuardAgent(base_dir=str(tmp_path), use_memory=False)
    ctx = AgentContext(id="ctx", workspace_root=str(tmp_path))
    call = SimpleNamespace(name="read_file_tool", input={"path": str(tmp_path / "a.py")})
    signature = agent._action_signature("tool", call.name, call.input)
    run = SimpleNamespace(
        task_id="task", ctx=ctx, action_evidence={
            signature: {
                "success": True,
                "workspace_fingerprint": await agent._workspace_fingerprint(ctx),
            }
        },
        no_progress_rounds=2, step=3, messages=[], action_errors=[],
        done=False, result=None, reasoning=None,
        # This agent has already changed something, so the third strike is fatal.
        produced_change=True, baseline_fingerprint=None,
    )
    decision = {
        "tool_calls": [call],
        "routing": {call.name: ("tool", call.name)},
        "reasoning": "repeat",
        "step_tokens": 1,
    }

    assert await agent._prepare_round(run, decision) is None
    assert run.done is False
    # The count is reported, not spelled out: the allowance now depends on the budget,
    # so "three" would be wrong for most runs that hit this.
    assert "Stopped after 3 no-progress" in run.result
    assert agent.concluded == 1


@pytest.mark.asyncio
async def test_guard_does_not_terminate_before_the_run_changes_anything(tmp_path):
    """Three repeated recon reads must not end a run that has produced nothing.

    Regression for a ProgramBench run that died at step 8 of 200 with no source file
    written: the agent was still reading the docs, and every repeat looked
    unproductive because the fingerprint it was compared against pointed at the host
    session directory while the agent was working inside a peer container.
    """
    await hook_manager.initialize(hook_names=["no_progress_hook"])
    agent = _GuardAgent(base_dir=str(tmp_path), use_memory=False)
    ctx = AgentContext(id="ctx", workspace_root=str(tmp_path))
    call = SimpleNamespace(name="read_file_tool", input={"path": str(tmp_path / "a.py")})
    signature = agent._action_signature("tool", call.name, call.input)
    run = SimpleNamespace(
        task_id="task", ctx=ctx, action_evidence={
            signature: {
                "success": True,
                "workspace_fingerprint": await agent._workspace_fingerprint(ctx),
            }
        },
        no_progress_rounds=2, step=3, messages=[], action_errors=[],
        done=False, result=None, reasoning=None,
        produced_change=False, baseline_fingerprint=None,
    )
    decision = {
        "tool_calls": [call],
        "routing": {call.name: ("tool", call.name)},
        "reasoning": "repeat",
        "step_tokens": 1,
    }

    assert await agent._prepare_round(run, decision) is None
    # Blocked and corrected, but still alive.
    assert run.no_progress_rounds == 3
    assert run.done is False
    assert run.result is None, "must not have concluded"
    # The guard asks for another turn by setting a flag, which `_advance`'s loop reads.
    # It used to call `_advance` itself: one nested frame per blocked proposal, against
    # Python's recursion limit, in a loop whose whole purpose is to repeat.
    assert run.retry_now is True
    assert "No-progress guard" in run.action_errors[0]


@pytest.mark.asyncio
async def test_guard_still_terminates_a_stuck_run_that_never_changes_anything(tmp_path):
    """The leniency is bounded — a genuinely stuck agent must still stop."""
    import agentevolver.agent.types as agent_types

    await hook_manager.initialize(hook_names=["no_progress_hook"])
    agent = _GuardAgent(base_dir=str(tmp_path), use_memory=False)
    ctx = AgentContext(id="ctx", workspace_root=str(tmp_path))
    call = SimpleNamespace(name="read_file_tool", input={"path": str(tmp_path / "a.py")})
    signature = agent._action_signature("tool", call.name, call.input)
    run = SimpleNamespace(
        task_id="task", ctx=ctx, action_evidence={
            signature: {
                "success": True,
                "workspace_fingerprint": await agent._workspace_fingerprint(ctx),
            }
        },
        no_progress_rounds=agent_types._NO_PROGRESS_STRIKES_BEFORE_ANY_CHANGE - 1,
        step=9, messages=[], action_errors=[],
        done=False, result=None, reasoning=None,
        produced_change=False, baseline_fingerprint=None,
    )
    decision = {
        "tool_calls": [call],
        "routing": {call.name: ("tool", call.name)},
        "reasoning": "repeat",
        "step_tokens": 1,
    }

    assert await agent._prepare_round(run, decision) is None
    assert run.done is False
    assert "no-progress" in (run.result or "").lower()


# --- a long budget must not be a recursion limit --------------------------------
#
# `_advance` used to call itself for every turn that continued immediately — a text-only
# reply, a proposal the guard blocked. One frame per step, against Python's default
# recursion limit of 1000. Raising max_step from 200 to 1000 turned that into a crash,
# and the crash surfaced as "maximum recursion depth exceeded" raised from inside a Jinja
# template parser: nothing in the traceback named the actual cause.

def test_advance_iterates_rather_than_recursing():
    import inspect

    from agentevolver.agent.types import Agent

    loop = inspect.getsource(Agent._advance)
    assert "while True:" in loop
    assert "_advance_once" in loop
    # The turn body must not call back into the loop.
    body = inspect.getsource(Agent._advance_once)
    assert "self._advance(" not in body


def test_a_thousand_immediate_retries_do_not_exhaust_the_stack():
    """The failure mode, reproduced: a model that cannot be called yields no tool calls,
    so every turn retries instantly."""
    import asyncio

    from agentevolver.agent.types import Agent

    turns = {"n": 0}

    class _Looper(Agent):
        name: str = "looper"
        description: str = "test"
        metadata: dict = {}

        async def _advance_once(self, run):
            turns["n"] += 1
            return turns["n"] < 1500

    agent = _Looper(base_dir="/tmp", max_step=2000)
    asyncio.run(agent._advance(object()))
    assert turns["n"] == 1500


# --- a broken model stops the run instead of consuming it -----------------------

@pytest.mark.asyncio
async def test_consecutive_model_errors_end_the_run_with_the_model_error():
    """Observed: 958 steps in 44 seconds, reported as a stack overflow, while the real
    cause — "Model ... not found. Available: [...]" — had been logged on step 1. A model
    that cannot be called returns no tool calls, which is indistinguishable from thinking
    out loud, so the turn retried for as long as the budget lasted."""
    from agentevolver.agent.types import _THINK_FAILURES_BEFORE_GIVING_UP, _AgentRun

    error = "Model nope/nope not found. Available: ['a', 'b']"

    class _BrokenModelAgent(_GuardAgent):
        name: str = "broken_model_agent"

        async def _constraint_check(self, task_id, ctx):
            return None, {}

        async def _get_messages(self, *args, **kwargs):
            return []

        async def _think(self, *args, **kwargs):
            return {"tool_calls": [], "routing": {}, "reasoning": "", "step_tokens": 0,
                    "error": error}

    agent = _BrokenModelAgent(base_dir="/tmp", max_step=1000)
    run = _AgentRun("task", None, AgentContext(), SimpleNamespace(name="ref"), "tid", {})

    turns = 0
    while await agent._advance_once(run):
        turns += 1
        assert turns < 20, "the run kept retrying a model it cannot call"

    assert run.think_failures == _THINK_FAILURES_BEFORE_GIVING_UP
    assert getattr(agent, "concluded", 0) == 1
    assert run.done is False
    # The reported reason has to be the model error, not whatever the budget hit later.
    assert error in (run.result or "")
    # And it stops early rather than spending the budget.
    assert run.step < 10


@pytest.mark.asyncio
async def test_a_recovered_model_call_clears_the_failure_count():
    """Three *consecutive* failures, so a transient upstream error does not end a run
    that goes on to work."""
    from agentevolver.agent.types import _AgentRun

    run = _AgentRun("task", None, AgentContext(), SimpleNamespace(name="ref"), "tid", {})
    run.think_failures = 2

    class _RecoveringAgent(_GuardAgent):
        name: str = "recovering_agent"

        async def _constraint_check(self, task_id, ctx):
            return None, {}

        async def _get_messages(self, *args, **kwargs):
            return []

        async def _think(self, *args, **kwargs):
            return {"tool_calls": [], "routing": {}, "reasoning": "thinking", "step_tokens": 0,
                    "error": None}

    agent = _RecoveringAgent(base_dir="/tmp", max_step=1000)
    assert await agent._advance_once(run) is True   # text-only turn: take another
    assert run.think_failures == 0
    assert getattr(agent, "concluded", 0) == 0


# --- terminating is a separate judgement from blocking ---------------------------

def test_strike_allowance_scales_with_the_step_budget():
    """A fixed count of 3 was calibrated when budgets were tens of steps. At 1000 it
    ended a run on step 27 — 2.7% of the budget, 973 steps left — while the agent was
    re-reading output it had correctly captured moments earlier. Worth pushing back on,
    not worth ending a run over that early."""
    from agentevolver.agent.types import (
        _NO_PROGRESS_STRIKE_BUDGET_FRACTION as fraction,
        _NO_PROGRESS_STRIKES_MAX as ceiling,
        _NO_PROGRESS_STRIKES_MIN as floor,
    )

    def allowance(max_step):
        return max(floor, min(int(max_step * fraction), ceiling))

    assert allowance(30) == floor          # a tiny budget still gets a few corrections
    assert allowance(200) > floor          # and a larger one gets proportionally more
    assert allowance(1000) == ceiling      # without spending a quarter of itself circling
    assert allowance(100000) == ceiling


def test_the_terminating_message_reports_the_actual_count():
    """It said "three" regardless of how many there had been, which is misleading once
    the allowance depends on the budget."""
    import inspect

    from agentevolver.agent.types import Agent

    source = inspect.getsource(Agent._prepare_round)
    assert "Stopped after {run.no_progress_rounds} no-progress" in source
    assert "Stopped after three" not in source
    # And the backstop still exists.
    assert "no_progress_rounds >= strikes_allowed" in source
