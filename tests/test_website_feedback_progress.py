"""Reproduce the historical retry/collection/plan ordering failures without an LLM."""

import json
from types import SimpleNamespace

import pytest

from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
from agentevolver.environment.default.job.environment import JobEnvironment
from agentevolver.tool.default.deployment.deploy import DeployTool


@pytest.fixture
def feedback_round(tmp_path, monkeypatch):
    from agentevolver.runtime import kernel

    # Release 1 needed a retry for P03. Release 2 therefore ends at 2, 2, 3,
    # exactly the shape that the historical uniform min_turns=3 wait mishandled.
    processes = {}
    for pid, turns in (("p01", 2), ("p02", 2), ("p03", 3)):
        processes[pid] = SimpleNamespace(
            pid=pid, turns=turns, alive=True, busy=False, mailbox=[], resident=True,
            state=SimpleNamespace(value="running"), error="", started_at=1,
            ended_at=None, session_id=pid,
            turn_results={turns - 1: "Prior release report", turns: f"{pid}: full R2 feedback"},
            turn_success={turns: True}, turn_diagnostics={},
        )
    monkeypatch.setattr(kernel, "get", processes.get)
    plan = tmp_path / "plan.md"
    plan.write_text("Initial plan: navigation, pickup, repair.\n")
    monkeypatch.setattr("agentevolver.plan.server.read_plan", lambda *a, **k: plan.read_text())
    monkeypatch.setattr("agentevolver.plan.server.plan_path", lambda *a, **k: plan)
    ctx = SimpleNamespace(id="feedback-replay", extra={
        "website_runtime_contract": {
            "subscriber_job_ids": list(processes), "collected_turns": {},
            "release_turn_floor": {"1": {p: 0 for p in processes},
                                   "2": {"p01": 1, "p02": 1, "p03": 2}},
        },
        "deployment_release_history": [{"release_number": 1}, {
            "release_number": 2, "source_revision": "r2-source", "release_url": "/s/demo--r2/",
        }],
    })
    return ctx, processes, plan


@pytest.mark.asyncio
async def test_retry_counts_can_be_waited_together_and_full_feedback_collected(feedback_round):
    ctx, processes, _ = feedback_round
    env = JobEnvironment()
    old = await env.wait(list(processes), min_turns=3, timeout=0.01, ctx=ctx)
    assert old["timed_out"]
    result = await env.wait(
        list(processes), min_turns_by_job={"p01": 2, "p02": 2, "p03": 3},
        timeout=0.1, ctx=ctx,
    )
    assert result["success"] and all(row["ready"] for row in result["jobs"])
    for row in result["jobs"]:
        report = await env.output(row["job_id"], turn=row["turns"], ctx=ctx)
        assert processes[row["job_id"]].turn_results[row["turns"]] in report["message"]
    assert DeployTool._previous_release_blocker(ctx) == ""
    assert ctx.extra["website_runtime_contract"]["collected_turns"] == {
        "p01": 2, "p02": 2, "p03": 3,
    }


@pytest.mark.asyncio
async def test_stale_report_read_cannot_acknowledge_r2(feedback_round):
    ctx, _, _ = feedback_round
    await JobEnvironment().output("p03", turn=2, ctx=ctx)
    assert DeployTool._acceptance_state(ctx.extra["website_runtime_contract"], 2, "p03") == "pending"
    builder = WebsiteBuilderAgent()
    builder.ctx = ctx
    state = await builder.feedback_progress(0)
    rows = json.loads(state.splitlines()[-2])["subscribers"]
    assert next(r for r in rows if r["job_id"] == "p03")["feedback"] == "unread"


@pytest.mark.asyncio
async def test_feedback_and_stale_plan_reach_the_next_builder_request(feedback_round, monkeypatch):
    from agentevolver.agent.context.conversation import Conversation
    from agentevolver.agent.loop.agent import Agent

    ctx, _, plan = feedback_round
    builder = WebsiteBuilderAgent()
    builder.ctx = ctx
    builder.middleware = [lambda agent, step: agent.feedback_progress(step)]

    async def no_environment(self, ctx):
        return ""

    monkeypatch.setattr(Agent, "environment_state", no_environment)
    conversation = Conversation(task="Improve the product from R2 feedback")
    envelope = builder.assembler.build_envelope(conversation, live=await builder._live_blocks(59))
    projected = "\n".join(message.text for message in envelope.live)
    assert '"feedback": "unread"' in projected and '"p03": 3' in projected
    assert not ctx.extra["website_runtime_contract"]["collected_turns"]

    await JobEnvironment().output("p01", turn=2, ctx=ctx)
    assert "PLAN UNCHANGED SINCE FEEDBACK READ: p01" in await builder.feedback_progress(60)
    plan.write_text("R2 P01: fix mirrored compass before journal; test spire left/right.\n")
    # Re-reading must not reset the baseline and incorrectly make the updated plan stale.
    await JobEnvironment().output("p01", turn=2, ctx=ctx)
    assert "PLAN UNCHANGED" not in await builder.feedback_progress(61)
    current = "\n".join(await builder._live_blocks(61))
    assert "test spire left/right" in current
    assert '"feedback": "unread"' in current  # P02/P03 still must be collected.


@pytest.mark.asyncio
async def test_completed_report_remains_collectable_after_subscriber_exits(feedback_round):
    ctx, processes, _ = feedback_round
    processes["p01"].alive = False
    result = await JobEnvironment().output("p01", turn=2, ctx=ctx)
    assert result["success"] and result["collected_turn"] == 2
    assert "p01: full R2 feedback" in result["message"]


@pytest.mark.asyncio
async def test_wait_map_validates_ids_and_preserves_default(feedback_round):
    ctx, processes, _ = feedback_round
    env = JobEnvironment()
    for targets in ({"unknown": 1}, {"p01": -1}):
        assert not (await env.wait(list(processes), min_turns_by_job=targets, ctx=ctx))["success"]
    result = await env.wait(list(processes), min_turns=2, min_turns_by_job={"p03": 3}, ctx=ctx)
    assert result["success"]
    assert [r["required_turns"] for r in result["jobs"]] == [2, 2, 3]
