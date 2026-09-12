"""Scenario inputs and callable consumers agree before a paid experiment starts."""
from types import SimpleNamespace

import pytest
from mmengine import Config

from examples.run_website_evolution_demo import (
    DEFAULT_CONFIG, SCENARIO_ROOT, build_task_text, parse_args, resolve_inputs, task_inputs,
)
from agentevolver.task.context import bind_manifest, parse_manifest


@pytest.mark.parametrize("name", ["arkbound_game", "lumen_museum", "orbital_simulator"])
def test_each_scenario_requires_multiple_entities_and_stages_its_materials(name):
    args = parse_args(["--scenario-dir", str(SCENARIO_ROOT / name)])
    config, brief = resolve_inputs(args)
    cfg = Config.fromfile(str(config))
    assert cfg.website_builder_agent.include_agents
    assert "agent" in cfg.website_builder_agent.accepts_evolved
    assert cfg.website_builder_agent.use_plan
    assert cfg.general_agent.model_name == cfg.website_builder_agent.model_name
    assert cfg.general_agent.env_names == []
    assert cfg.general_agent.capability_allowlists.tool == ["done_tool"]
    task = build_task_text(brief)
    manifest = parse_manifest(task)[2]
    assert set(manifest["evolution"]["required_modules"]) == {"skill", "agent", "connector"}
    inputs = task_inputs(brief)
    assert len(inputs) == 2 and all(path.is_file() for path in inputs)
    staged = [f"/session/inputs/{i}_{path.name}" for i, path in enumerate(inputs)]
    bound = bind_manifest(task, staged)[2]
    assert bound["attachments"][1]["path"] == staged[1]
    assert bound["attachments"][1]["name"] == inputs[1].name
    assert bound["subscribers"] == []
    assert "record_use" not in task


def test_commonspace_and_explicit_config_override_remain_supported():
    config, brief = resolve_inputs(parse_args([
        "--scenario-dir", str(SCENARIO_ROOT / "commonspace_forum"),
    ]))
    assert config == DEFAULT_CONFIG
    assert "required_modules" not in parse_manifest(build_task_text(brief))[2]["evolution"]
    config, _ = resolve_inputs(parse_args([
        "--scenario-dir", str(SCENARIO_ROOT / "lumen_museum"),
        "--config", str(DEFAULT_CONFIG),
    ]))
    assert config == DEFAULT_CONFIG


@pytest.mark.asyncio
async def test_new_specialist_becomes_callable_without_giving_it_parent_state(monkeypatch):
    from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
    from agentevolver.agent.actor.general_agent import GeneralAgent
    from agentevolver.agent.context import capabilities
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.extension import extension_manager
    from agentevolver.runtime.kernel import child_context

    config, _ = resolve_inputs(parse_args([]))
    cfg = Config.fromfile(str(config))
    builder = WebsiteBuilderAgent(**cfg.website_builder_agent)
    worker = GeneralAgent(**cfg.general_agent)
    manifest = SimpleNamespace(components=[])
    monkeypatch.setattr(type(extension_manager), "read_manifest", lambda self: manifest)

    async def assemble(agent, ctx, **kwargs):
        assert kwargs["include_agents"]
        return [], {name: ("agent", name) for name in ctx.extra["agent_allowlist"]}

    monkeypatch.setattr(capabilities, "assemble_native_tools", assemble)
    router = CapabilityRouter(include_agents=builder.include_agents)
    ctx = SimpleNamespace(id="builder", extra={"task_manifest": {"evolution": {
        "required_modules": ["agent"],
    }}, "task_state": {"private": "parent work"}})
    _, routes = await router.schemas(builder, ctx)
    assert "expedition_designer" not in routes
    manifest.components.append(SimpleNamespace(module="agent", name="expedition_designer"))
    _, routes = await router.schemas(builder, ctx)
    assert routes["expedition_designer"] == ("agent", "expedition_designer")
    child = child_context(worker, {"task": "Design from the supplied packet."}, builder, ctx)
    assert "task_state" not in child.extra and "task_manifest" not in child.extra
