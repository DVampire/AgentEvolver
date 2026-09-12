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
#: What the policy needs routable before it will render at all — the means to inspect, the
#: means to record an adoption, and the skill that says how. Three worker agents used to be
#: on this list; the work is the agent's own now, so their absence must not switch the
#: policy off, and every remaining member's absence must.
ROUTES = {
    name: (kind, name) for kind, name in (
        ("tool", "adoption_tool"), ("tool", "inspect_tool"),
        ("skill", "self_evolving_skill"),
    )
}


def test_evolution_notice_accepts_native_environment_evidence(monkeypatch):
    from agentevolver.task import evolution

    monkeypatch.setattr(evolution, "status", lambda ctx: {
        "required": True, "ready": False, "reasons": ["missing consumer evidence"]})
    notice = evolution.live_notice(SimpleNamespace())
    assert "environment interaction" in notice
    assert "browser" not in notice


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
@pytest.mark.parametrize("cls, task", [
    (MetaAgent, "Build a small usable site."),
    (MetaAgent, "Fix the repository issue and verify it with local tests."),
    (WebsiteBuilderAgent, "Build a small usable site."),
])
@pytest.mark.parametrize("deferred", [False, True])
async def test_policy_reaches_real_prompt_without_evolution_task(cls, task, deferred, bound_session):
    agent = cls(enable_evolving=False)  # Target mutability is not the runtime switch.
    agent.task = task
    ctx = SimpleNamespace(id=f"policy-{cls.__name__}-{deferred}", extra={})
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], {} if deferred else ROUTES)))
    if deferred:
        remember_catalog(ctx, agent.name, [{"name": n, "route": r} for n, r in ROUTES.items()])
    try:
        values = await agent.prompt_modules(ctx)
        assert values["evolution_enabled"] is True
        # The same decision reaches the stable plan instructions, even when deferred.
        from agentevolver.plan.server import plan_manager

        agent.middleware = []
        agent.ctx = ctx
        agent.environment_state = AsyncMock(return_value="")
        await agent._live_blocks(0)
        rules = plan_manager.instructions(enabled=agent.use_plan, evolution_enabled=values["evolution_enabled"])
        assert "Evolution opportunities" in rules
        assert "verification boundaries" in rules
        # The decision must reach live planning. It used to say "dispatch in the
        # background"; the work is the agent's own now, so what has to arrive is that it
        # starts now and records the version it produced.
        assert "Launch qualifying work now" in rules
        assert "registered version" in rules
        assert not plan_manager.active(ctx.id)
        cfg = parse_prompt_file(str(ROOT / "agentevolver/prompt/default" / f"{agent.name}.html"))
        message = await cfg.to_prompt().get_system_message(values, reload=True)
        rendered = " ".join(message.text.split())
        assert message.text.count("<self-evolution-rules>") == 1
        assert "Do not wait for a task to mention evolution" in message.text
        assert "Repeated cost or inconsistency" in message.text
        assert "Improve an evolvable target, or write a new one" in rendered
        assert "Change and evaluation stay sequential" in rendered
        assert "Before finishing, join and close" in rendered
        assert "Task-required evidence" in rendered
        assert "evolution.require_verified_improvement" in rendered
        assert "record_use" in rendered and "capability_gap" in rendered
        assert "observation and baseline call IDs before registering" in rendered
        assert "Self-observations and runtime status notices are not user feedback" in rendered
        for opportunity in ("Reusable learning", "Expected reuse", "Better method",
                            "Missing capability", "New experience", "Self-verification",
                            "before implementation fails", "repeated failure is not required"):
            assert opportunity in rendered
        for guard in ("preserve consumer permission boundaries", "exact candidate version",
                      "passing that version-scoped report", "roll back or unload",
                      "At CRITICAL, start no new experiment"):
            assert guard in rendered
        for kind in COMPONENT_TYPE_NAMES:
            assert kind.lower() in message.text.lower()
        assert "Choose the form" in rendered
        assert "do not default to a new Tool" in rendered
        assert "a reusable procedure, design/review method" in rendered
        assert "Agent/prompt" in rendered and "reusable reasoning, planning" in rendered
        assert "an Agent improvement need not create another agent" in rendered
        assert "consumer can actually load and exercise the chosen form" in rendered
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
    assert "even when the task does not request it" in builder


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


def test_the_conventions_support_bounded_verified_improvements():
    """The per-operation conventions, wherever they live, still bound the work.

    They were three worker skills read by three agents; they are one file now, sectioned by
    operation. What has to survive is the substance: a change edits through the patch tools
    rather than overwriting whole files, a frozen target stops the run, and an evaluation
    names an independent case, its safety checks, and what it could not test.
    """
    conventions = (
        ROOT / "agentevolver/skill/evolving/self_evolving_skill/references/conventions.md"
    ).read_text()
    assert "apply_patch_tool" in conventions and "bash_tool" in conventions
    assert "edit_file_tool" not in conventions and "write_file_tool" not in conventions
    assert "enable_evolving" in conventions and "Frozen means stop" in conventions
    for expected in ("independent reuse or regression case", "required safety checks",
                     "untested limits", "inconclusive", "Reading instructions alone"):
        assert expected in conventions


@pytest.mark.parametrize("scenario_name", ["arkbound_game", "commonspace_forum", "lumen_museum", "orbital_simulator"])
def test_demo_requires_experiment_but_keeps_product_brief_independent(scenario_name):
    from examples.run_website_evolution_demo import build_task_text
    from agentevolver.task.context import parse_manifest

    scenario = ROOT / "examples/tasks/website_evolution" / scenario_name
    task = build_task_text(scenario / "scenario.html")
    assert parse_manifest(task)[2]["evolution"]["require_verified_improvement"] is True
    assert "self_evolving_skill" not in task
    assert "record_use" not in task
    assert "self_evolving_skill" not in (scenario / "scenario.html").read_text()
    for forbidden in ("generate_agent", "optimize_agent",
                      "target_type", "minimum_kept_evolutions", "must evolve"):
        assert forbidden not in task
    # Product design remains open while the brief supplies an observable contract.
    source = (scenario / "scenario.html").read_text()
    for section in ("product-intent", "creative-freedom", "open-horizons", "quality-evidence"):
        assert f'id="{section}"' in source
    assert "source hashes and repeated deployments" in " ".join(task.lower().split())


@pytest.mark.asyncio
async def test_evolution_stays_enabled_without_child_agents(bound_session):
    agent = WebsiteBuilderAgent(include_agents=False)
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], ROUTES)))
    values = await agent.prompt_modules(SimpleNamespace(id="solo", extra={}))
    assert values["evolution_enabled"] is True


def test_evolution_skill_does_not_require_removed_workers():
    source = (ROOT / "agentevolver/skill/evolving/self_evolving_skill/SKILL.md").read_text()
    assert "Start a qualifying opportunity now in your own action loop" in source
    assert "use `run_in_background=true`" not in source
    assert "Every dispatch names one as `target_type`" not in source
