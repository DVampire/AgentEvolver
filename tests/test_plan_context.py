"""Planning reaches coordinator requests, survives folding, and stays off workers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.actor.code_agent import CodeAgent
from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
from agentevolver.agent.actor.website_user_agent import WebsiteUserAgent
from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.loop.agent import Agent
from agentevolver.message import CompactionMessage, SystemMessage
from agentevolver.plan.server import PlanManagerServer
from agentevolver.plan.types import PlanMode


@pytest.fixture
def planning(tmp_path, monkeypatch):
    manager = PlanManagerServer.__new__(PlanManagerServer)
    manager._states = {}
    monkeypatch.setattr("agentevolver.plan.server.plan_manager", manager)
    monkeypatch.setattr(
        "agentevolver.plan.server.plan_path",
        lambda session_id="", *, owner="": tmp_path / f"{session_id}.md",
    )

    async def environment_state(self, ctx):
        return ""

    monkeypatch.setattr(Agent, "environment_state", environment_state)
    return manager, tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [MetaAgent, WebsiteBuilderAgent])
@pytest.mark.parametrize("evolving", [False, True])
async def test_coordinator_reads_latest_plan_after_feedback_and_folding(planning, actor, evolving):
    manager, root = planning
    agent = actor(enable_evolving=not evolving)  # Target mutability is independent.
    agent.middleware = []
    agent.ctx = SimpleNamespace(id="coordinator", extra={})
    routes = {
        name: (kind, name) for kind, name in (
            ("agent", "generate_agent"), ("agent", "optimize_agent"),
            ("agent", "evaluate_agent"), ("tool", "adoption_tool"),
            ("tool", "inspect_tool"), ("skill", "self_evolving_skill"),
        )
    }
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], routes if evolving else {})))
    await agent.prompt_modules(agent.ctx)
    conversation = Conversation(task="Build and improve the website")
    conversation.system = [SystemMessage(content="Stable instructions")]
    plan = root / "coordinator.md"
    missing = "\n".join(await agent._live_blocks(0))
    assert "No plan.md exists yet" in missing and str(plan) in missing
    assert ("Evolution opportunities" in missing) is evolving
    assert not plan.exists()  # The coordinator authors it; runtime never fabricates a plan.
    opportunity = (
        "\n## Evolution opportunities\n"
        "E1 deferred: bounded browser observation; evidence call-17 returned an entire scene. "
        "Consumer: next gallery preview. Revisit after the first working preview.\n"
    ) if evolving else ""
    plan.write_text("Initial approach: ship a gallery." + opportunity)
    before = agent.assembler.build_envelope(conversation, live=await agent._live_blocks(0))
    assert "ship a gallery" in before.live[0].text
    assert all("ship a gallery" not in m.text for m in before.fixed)

    await agent.on_event(SimpleNamespace(text="Participant 2 requests an undo action."), None)
    live = await agent._live_blocks(1)
    assert "Participant 2 requests an undo action" in "\n".join(live)
    assert "Before implementing a change" in "\n".join(live)
    # The coordinator's next action updates the actual shared document.
    revised_opportunity = opportunity.replace(
        "E1 deferred", "E1 probing",
    ).replace(
        "Revisit after the first working preview.",
        "Compare output size and diagnostic coverage; check a second page before adoption.",
    )
    plan.write_text(
        "Replanned: add undo for Participant 2; verify restore and reload." + revised_opportunity
    )
    conversation.checkpoint = CompactionMessage(content="Older conversation was folded.")
    after = agent.assembler.build_envelope(conversation, live=await agent._live_blocks(2))
    assert [m.text for m in before.fixed] == [m.text for m in after.fixed]
    assert "add undo for Participant 2" in after.live[0].text
    assert "ship a gallery" not in after.live[0].text
    if evolving:
        assert "E1 probing" in after.live[0].text
        assert "call-17" in after.live[0].text
        assert "E1 deferred" not in after.live[0].text
        assert all("Evolution opportunities" not in m.text for m in after.fixed)
    assert not after.live[0].cache
    assert not manager.active("coordinator")
    agent.router.schemas.assert_awaited_once()  # Live planning does not rediscover every step.


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [CodeAgent, WebsiteUserAgent])
@pytest.mark.parametrize("evolving", [False, True])
async def test_workers_do_not_read_or_create_automatic_plans(planning, actor, evolving):
    _, root = planning
    (root / "parent.md").write_text("Coordinator-only plan")
    agent = actor(enable_evolving=evolving)
    agent.middleware = []
    agent.ctx = SimpleNamespace(id="worker", parent_session_id="parent", extra={})
    assert not agent.use_plan
    assert not any("plan-context" in block for block in await agent._live_blocks(0))
    assert not (root / "worker.md").exists()


def test_explicit_modes_preserve_review_gate_and_off_semantics(planning):
    manager, root = planning
    manager.set_mode("coordinator", PlanMode.OFF)
    assert manager.context("coordinator", enabled=True) == ""
    manager.enter("coordinator")
    context = manager.context("coordinator", enabled=True)
    assert "exit_plan_mode" in context and 'active="true"' in context
    # An explicitly gated worker must also see how to leave the gate.
    assert "exit_plan_mode" in manager.context("coordinator", enabled=False)
    manager.approve("coordinator", "Approved approach: build undo.")
    assert (root / "coordinator.md").read_text() == "Approved approach: build undo."
    assert 'active="false"' in manager.context("coordinator", enabled=True)
    assert "build undo" in manager.context("coordinator", enabled=True)


def test_evolution_planning_respects_off_and_explicit_worker_gate(planning):
    manager, _ = planning
    manager.set_mode("coordinator", PlanMode.OFF)
    assert manager.context("coordinator", enabled=True, evolution_enabled=True) == ""
    manager.enter("coordinator")
    context = manager.context("coordinator", enabled=True, evolution_enabled=True)
    assert "Evolution opportunities" in context
    assert "exit_plan_mode" in context and manager.active("coordinator")
    # An explicit worker review gate must not opt the worker into coordinator planning.
    worker_context = manager.context("coordinator", enabled=False, evolution_enabled=True)
    assert "exit_plan_mode" in worker_context
    assert "Evolution opportunities" not in worker_context


def test_plan_projection_is_bounded_and_names_the_full_document(planning):
    manager, root = planning
    (root / "coordinator.md").write_text("x" * 20_000)
    context = manager.context("coordinator", enabled=True)
    assert "Plan excerpt truncated" in context
    assert str(root / "coordinator.md") in context
    assert len(context) < 18_000


def test_website_launcher_defaults_to_automatic_planning():
    from examples.run_website_evolution_demo import parse_args

    assert parse_args([]).plan_mode == "auto"
    assert parse_args(["--plan-mode", "off"]).plan_mode == "off"
    assert parse_args(["--plan-mode", "plan"]).plan_mode == "plan"
