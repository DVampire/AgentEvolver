"""Premature handoff regression: real budgets, explicit outcomes and context placement."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from agentevolver.agent.loop import Agent, Decision
from agentevolver.agent.loop.guards import resource_status
from agentevolver.agent.loop.router import CapabilityRouter
from agentevolver.message.types import AssistantMessage, SystemMessage
from agentevolver.runtime.process import RunBudget
from agentevolver.tool.default.lifecycle.done import DoneTool
from test_agent_loop import Scripted, StubRouter, calls


def context():
    return SimpleNamespace(id="completion", extra={"task_manifest": {
        "run_policy": {"require_completion_outcome": True},
    }})


def finish(outcome="completed", unmet=()):
    return calls(("done_tool", {"reasoning": "Review of remaining work and resources",
        "result": "Saved the evidence and current conclusion", "outcome": outcome,
        "unmet_requirements": list(unmet)}))


class DoneRouter(StubRouter):
    def __init__(self):
        super().__init__({"done_tool": None, "experiment": lambda args: "Revised and evaluated hypothesis"})

    async def invoke(self, call, **kwargs):
        if call.name != "done_tool":
            return await super().invoke(call, **kwargs)
        self.invoked.append(call.name)
        response = await DoneTool()(**call.args)
        result = CapabilityRouter._from_response(call, response)
        return replace(result, final=result.ok)


@pytest.mark.asyncio
async def test_budget_is_live_by_default_updates_without_charging_and_survives_fold():
    agent = Agent(max_step=10_000, max_token=1_000_000_000, timeout=28_800)
    agent._started_at = time.time()
    agent.conversation.set_system([SystemMessage(content="Research")])
    for _ in range(6):
        agent.conversation.note("Next experiment")
        agent.conversation.append(AssistantMessage(content="Recorded the result"))
    agent.step, agent._used_tokens = 284, 16_096_275
    assert agent.middleware == []
    live = await agent._live_blocks(agent.step)
    text = "\n".join(live)
    assert "284 / 10,000 (9,716 steps remaining)" in text
    assert "16,096,275 / 1,000,000,000 (983,903,725 tokens remaining)" in text
    assert "28800s" in text and "Status: NORMAL" in text
    before = agent.assembler.build_envelope(agent.conversation, live=live)
    agent.step += 1
    agent._used_tokens += 100
    after = agent.assembler.build_envelope(agent.conversation, live=await agent._live_blocks(agent.step))
    assert before.fixed == after.fixed and before.recent == after.recent
    assert before.live != after.live
    assert "<budget>" not in str(agent.conversation.foldable(keep_turns=1))
    agent.conversation.fold("Completed experiments; next hypothesis remains", keep_turns=1)
    refreshed = await agent._live_blocks(agent.step)
    assert "16,096,375" in "\n".join(refreshed)
    assert "<budget>" not in str(agent.conversation.checkpoint)
    assert agent._used_tokens == 16_096_375 and agent.step == 285
    agent.assembler.build(agent.conversation, live=refreshed)


def test_shared_budget_reservations_are_visible_without_double_charging():
    agent = Agent(max_step=10_000, max_token=1_000_000)
    budget = RunBudget(limit=100_000, tokens=90_000)
    agent.proc = SimpleNamespace(budget=budget)
    agent._used_tokens = 10_000
    with budget.request("test-model", 5_000) as settle:
        first = resource_status(agent)
        assert resource_status(agent) == first
        session = next(s for s in first if s.name.startswith("session_tokens"))
        assert session.used == 95_000 and session.remaining == 5_000
        assert budget.tokens == 90_000 and budget.reserved == 5_000
        settle({"input_tokens": 1_000, "output_tokens": 100})
    assert next(s for s in resource_status(agent) if s.name.startswith("session_tokens")).used == 91_100


@pytest.mark.asyncio
async def test_false_resource_boundary_returns_to_work_then_can_complete():
    early = finish("resource_limited", ["Investigate the next factor and strategy hypothesis"])
    early.usage = {"context_input_tokens": 16_000_000, "output_tokens": 96_275}
    router = DoneRouter()
    agent = Scripted([early, calls(("experiment", {})), finish()], router=router,
                     max_step=10_000, max_token=1_000_000_000, timeout=28_800)
    result = await agent("Continue until the research completion review is satisfied", ctx=context())
    assert result.success and result.data["completion"]["outcome"] == "completed"
    assert router.invoked == ["done_tool", "experiment", "done_tool"]
    assert "Runtime resources are available" in str(agent.seen_live[1])
    assert "983,903,725 tokens remaining" in str(agent.seen_live[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("early", [
    Decision(text="Here is an interrupted partial report"),
    calls(("done_tool", {"reasoning": "handoff", "result": "partial"})),
    finish("completed", ["Required ablation has not been executed"]),
])
async def test_required_receipt_cannot_be_bypassed_and_unfinished_work_returns_to_loop(early):
    router = DoneRouter()
    agent = Scripted([early, calls(("experiment", {})), finish()], router=router, max_step=100)
    result = await agent("Complete the requested research", ctx=context())
    assert result.success and agent.step == 2 and "experiment" in router.invoked
    assert "Completion deferred" in str(agent.seen_live[1])
    assert agent.conversation.complete


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,usage,steps", [
    ("blocked", 0, 100), ("resource_limited", 86, 100), ("resource_limited", 0, 3),
])
async def test_legitimate_partial_handoff_preserves_result_but_is_not_success(outcome, usage, steps):
    ending = finish(outcome, ["Acquire the unavailable dataset"])
    ending.usage = {"input_tokens": usage}
    agent = Scripted([ending], router=DoneRouter(), max_step=steps, max_token=100)
    result = await agent("Perform the research", ctx=context())
    assert not result.success and agent.step == 0
    assert result.data["completion"]["outcome"] == outcome
    assert result.message == "Saved the evidence and current conclusion"


@pytest.mark.asyncio
async def test_repeated_unsupported_endings_fail_instead_of_succeeding_or_looping_forever():
    agent = Scripted([finish("resource_limited", ["More experiments remain"]) for _ in range(10)],
                     router=DoneRouter(), max_step=10_000, max_token=1_000_000_000)
    result = await agent("Continue useful research", ctx=context())
    assert not result.success and agent.step == 3
    assert "Unsupported completion" in result.message
    assert "completion" not in result.data


@pytest.mark.asyncio
async def test_negative_research_conclusion_can_complete_without_a_supported_strategy():
    agent = Scripted([finish()], router=DoneRouter(), max_step=100)
    result = await agent("Determine whether evidence supports the hypothesis; a negative finding is valid",
                         ctx=context())
    assert result.success and result.data["completion"]["outcome"] == "completed"
