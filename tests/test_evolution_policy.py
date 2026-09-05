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
@pytest.mark.parametrize("cls", [MetaAgent, WebsiteBuilderAgent])
@pytest.mark.parametrize("deferred", [False, True])
async def test_policy_reaches_real_prompt_without_evolution_task(cls, deferred):
    agent = cls(enable_evolving=False)  # Target mutability is not the runtime switch.
    agent.task = "Build a small usable site."
    ctx = SimpleNamespace(id=f"policy-{cls.__name__}-{deferred}", extra={})
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], {} if deferred else ROUTES)))
    if deferred:
        remember_catalog(ctx, agent.name, [{"name": n, "route": r} for n, r in ROUTES.items()])
    try:
        values = await agent.prompt_modules(ctx)
        assert values["evolution_enabled"] is True
        cfg = parse_prompt_file(str(ROOT / "agentevolver/prompt/default" / f"{agent.name}.html"))
        message = await cfg.to_prompt().get_system_message(values, reload=True)
        assert message.text.count("<self-evolution-rules>") == 1
        assert "Do not wait for a task to mention evolution" in message.text
        assert "Repeated cost or inconsistency" in message.text
        for kind in COMPONENT_TYPE_NAMES:
            assert kind.lower() in message.text.lower()
    finally:
        forget(ctx, agent.name)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", list(ROUTES))
async def test_incomplete_roster_cannot_enable_policy_with_task_words(missing):
    agent = MetaAgent(enable_evolving=True)
    agent.task = "You must evolve all eight components."
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], {
        n: r for n, r in ROUTES.items() if n != missing})))
    values = await agent.prompt_modules(SimpleNamespace(id=f"missing-{missing}", extra={}))
    assert values["evolution_enabled"] is False


@pytest.mark.asyncio
async def test_read_only_policy_stays_off_even_with_full_roster():
    agent = MetaAgent(permission_mode="read_only")
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], ROUTES)))
    values = await agent.prompt_modules(None)
    assert values["evolution_enabled"] is False
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


def test_echo_task_never_requests_component_evolution():
    from examples.run_website_evolution_demo import build_task_text

    scenario = ROOT / "examples/tasks/website_evolution/echo_ark"
    personas = [scenario / f"persona_{index:02d}.html" for index in range(1, 4)]
    task = build_task_text(scenario / "scenario.html", personas)
    for forbidden in ("self_evolving_skill", "generate_agent", "optimize_agent",
                      "target_type", "minimum_kept_evolutions", "must evolve"):
        assert forbidden not in task
        assert all(forbidden not in path.read_text() for path in personas)
    for outcome in ("conversation", "personal", "preview", "undo", "confirmed"):
        assert outcome in task.lower()
    assert "source hashes and repeated deployments" in " ".join(task.lower().split())
