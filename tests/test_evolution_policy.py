"""Autonomous evolution is a system policy, not a keyword in a product task."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
from agentevolver.agent.context.capabilities import forget, remember_catalog
from agentevolver.capability.types import COMPONENT_TYPE_NAMES
from agentevolver.prompt.types import parse_prompt_file

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    name: (kind, name) for kind, name in (
        ("agent", "generate_agent"), ("agent", "optimize_agent"),
        ("agent", "evaluate_agent"), ("tool", "adoption_tool"),
        ("tool", "inspect_tool"), ("skill", "self_evolving_skill"),
    )
}


@pytest.mark.asyncio
@pytest.mark.parametrize("arm, enabled", [("swebench_pro_agent", True),
                                         ("swebench_pro_agent_baseline", False)])
async def test_shipped_swe_roster_enables_only_evolution_arm(arm, enabled, bound_session):
    """A loadable benchmark config must also satisfy the runtime policy gate."""
    import argparse
    import contextlib
    import io
    from agentevolver.config import config

    with contextlib.redirect_stdout(io.StringIO()):
        config.initialize(config_path=str(ROOT / "configs" / f"{arm}.py"),
                          args=argparse.Namespace())
    routes = {name: (kind, name) for kind, names in (
        ("agent", config.agent_names), ("tool", config.tool_names),
        ("skill", config.skill_names)) for name in names}
    agent = MetaAgent(**config.meta_agent)
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], routes)))
    values = await agent.prompt_modules(SimpleNamespace(id=f"shipped-{arm}", extra={}))
    assert values["evolution_enabled"] is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [MetaAgent, WebsiteBuilderAgent])
@pytest.mark.parametrize("deferred", [False, True])
async def test_policy_reaches_real_prompt_without_evolution_task(cls, deferred, bound_session):
    agent = cls(enable_evolving=False)  # Target mutability is not the runtime switch.
    agent.task = "Build a small usable site."
    ctx = SimpleNamespace(id=f"policy-{cls.__name__}-{deferred}", extra={})
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], {} if deferred else ROUTES)))
    if deferred:
        remember_catalog(ctx, agent.name, [{"name": n, "route": r} for n, r in ROUTES.items()])
    try:
        values = await agent.prompt_modules(ctx)
        assert values["evolution_enabled"] is True
        # The same decision must reach live planning, including deferred capabilities.
        from agentevolver.plan.server import plan_manager

        agent.middleware = []
        agent.ctx = ctx
        agent.environment_state = AsyncMock(return_value="")
        assert "Evolution opportunities" in "\n".join(await agent._live_blocks(0))
        assert not plan_manager.active(ctx.id)
        cfg = parse_prompt_file(str(ROOT / "agentevolver/prompt/default" / f"{agent.name}.html"))
        message = await cfg.to_prompt().get_system_message(values, reload=True)
        rendered = " ".join(message.text.split())
        assert message.text.count("<self-evolution-rules>") == 1
        assert "Do not wait for a task to mention evolution" in message.text
        assert "Repeated cost or inconsistency" in message.text
        for opportunity in ("Reusable learning", "Expected reuse", "Better method",
                            "Missing capability", "New experience",
                            "before implementation fails", "repeated failure is not required"):
            assert opportunity in rendered
        for guard in ("preserve consumer permission boundaries", "exact candidate version",
                      "completed evaluator's `run_id`", "roll back or unload",
                      "At CRITICAL, start no new experiment"):
            assert guard in rendered
        for kind in COMPONENT_TYPE_NAMES:
            assert kind.lower() in message.text.lower()
    finally:
        forget(ctx, agent.name)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", list(ROUTES))
async def test_incomplete_roster_cannot_enable_policy_with_task_words(missing, bound_session):
    agent = MetaAgent(enable_evolving=True)
    agent.task = "You must evolve all eight components."
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], {
        n: r for n, r in ROUTES.items() if n != missing})))
    values = await agent.prompt_modules(SimpleNamespace(id=f"missing-{missing}", extra={}))
    assert values["evolution_enabled"] is False
    agent.middleware = []
    agent.environment_state = AsyncMock(return_value="")
    assert "Evolution opportunities" not in "\n".join(await agent._live_blocks(0))


@pytest.mark.asyncio
async def test_read_only_policy_stays_off_even_with_full_roster(bound_session):
    agent = MetaAgent(permission_mode="read_only")
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], ROUTES)))
    values = await agent.prompt_modules(None)
    assert values["evolution_enabled"] is False
    agent.middleware = []
    agent.environment_state = AsyncMock(return_value="")
    assert "Evolution opportunities" not in "\n".join(await agent._live_blocks(0))
    agent.router.schemas.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["meta_agent", "website_builder_agent"])
async def test_disabled_policy_is_not_rendered(name):
    cfg = parse_prompt_file(str(ROOT / "agentevolver/prompt/default" / f"{name}.html"))
    message = await cfg.to_prompt().get_system_message({"evolution_enabled": False}, reload=True)
    assert "<self-evolution-rules>" not in message.text


def test_orchestrators_use_one_shared_policy():
    for name in ("meta_agent", "website_builder_agent"):
        source = (ROOT / "agentevolver/prompt/default" / f"{name}.html").read_text()
        assert source.count('<module src="../module/evolution_rules.html"></module>') == 1
        assert "<self-evolution-rules>" not in source
        assert "<capability-evolution>" not in source
    builder = (ROOT / "agentevolver/prompt/default/website_builder_agent.html").read_text()
    assert "does not require the task to request evolution" in builder


def test_evolution_skill_has_no_second_failure_or_memory_gate():
    source = (ROOT / "agentevolver/skill/evolving/self_evolving_skill/SKILL.md").read_text()
    assert "shared `evolution_rules` system-prompt module owns the detection policy" in source
    for expected in ("Do not apply a second, stricter trigger gate", "expected reuse",
                     "writing a memory file is not a prerequisite", "hypotheses",
                     "action steps, not separate product releases"):
        assert expected in source
    for obsolete in ("Your conversation starts empty every turn", "≥2×",
                     "Do not evolve** on a first-time fixable defect",
                     "when the budget is TIGHT or CRITICAL", "no promotion step"):
        assert obsolete not in source
    for guard in ("enable_evolving", "exact candidate version", "adoption_tool",
                  "rollback", "unload", "inconclusive", "necessary permissions"):
        assert guard in source


def test_worker_instructions_support_bounded_verified_improvements():
    skills = ROOT / "agentevolver/skill/evolving"
    optimize = (skills / "optimize_skill/SKILL.md").read_text()
    assert "apply_patch_tool" in optimize and "bash_tool" in optimize
    assert "edit_file_tool" not in optimize and "write_file_tool" not in optimize
    assert "enable_evolving" in optimize and "Frozen means stop" in optimize
    for path in (skills / "evaluate_skill/SKILL.md",
                 ROOT / "agentevolver/prompt/default/evaluate_agent.html"):
        text = path.read_text()
        for expected in ("independent reuse or regression case", "required safety checks",
                         "untested limits", "inconclusive", "Reading instructions alone"):
            assert expected in text


@pytest.mark.parametrize("scenario_name", ["arkbound_game", "commonspace_forum", "lumen_museum", "orbital_simulator"])
def test_product_task_leaves_evolution_to_shared_policy(scenario_name):
    from examples.run_website_evolution_demo import build_task_text

    scenario = ROOT / "examples/tasks/website_evolution" / scenario_name
    personas = [scenario / f"persona_{index:02d}.html" for index in range(1, 4)]
    task = build_task_text(scenario / "scenario.html", personas)
    for forbidden in ("self_evolving_skill", "generate_agent", "optimize_agent",
                      "target_type", "minimum_kept_evolutions", "must evolve"):
        assert forbidden not in task
        assert all(forbidden not in path.read_text() for path in personas)
    # Product design remains open while the brief supplies an observable contract.
    source = (scenario / "scenario.html").read_text()
    for section in ("product-intent", "creative-freedom", "open-horizons", "quality-evidence"):
        assert f'id="{section}"' in source
    assert "source hashes and repeated deployments" in " ".join(task.lower().split())
