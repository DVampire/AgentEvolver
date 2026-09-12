"""Scenario inputs and callable consumers agree before a paid experiment starts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    assert config == DEFAULT_CONFIG
    cfg = Config.fromfile(str(config))
    assert cfg.agent_names == ["website_builder_agent"]
    assert "general_agent" not in cfg
    assert cfg.website_builder_agent.include_agents
    assert cfg.website_builder_agent.use_plan
    assert "agent" not in cfg.website_builder_agent.capability_allowlists
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


def test_commonspace_and_explicit_config_override_remain_supported(tmp_path):
    config, brief = resolve_inputs(parse_args([
        "--scenario-dir", str(SCENARIO_ROOT / "commonspace_forum"),
    ]))
    assert config == DEFAULT_CONFIG
    assert "required_modules" not in parse_manifest(build_task_text(brief))[2]["evolution"]
    custom = tmp_path / "custom_demo.py"
    custom.write_text("agent_names = ['website_builder_agent']\n")
    config, _ = resolve_inputs(parse_args([
        "--scenario-dir", str(SCENARIO_ROOT / "lumen_museum"),
        "--config", str(custom),
    ]))
    assert config == custom


@pytest.mark.asyncio
async def test_new_specialist_becomes_callable_without_giving_it_parent_state(monkeypatch):
    from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
    from agentevolver.agent.context import capabilities
    from agentevolver.agent.loop.decision import ActionCall
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.extension import extension_manager
    from agentevolver.runtime.kernel import child_context

    config, _ = resolve_inputs(parse_args([]))
    cfg = Config.fromfile(str(config))
    builder = WebsiteBuilderAgent(**cfg.website_builder_agent)
    worker = SimpleNamespace(name="expedition_designer")
    registered = {builder.name: SimpleNamespace(name=builder.name, description="Own the website")}
    revision = 0
    manager = SimpleNamespace(
        list=lambda: list(registered), get_info=lambda name: registered.get(name),
        get_schema=lambda name, **kwargs: {
            "type": "function", "function": {"name": name, "description": "Design an expedition",
            "parameters": {"type": "object", "properties": {"task": {"type": "string"}}}},
        },
    )
    monkeypatch.setattr(capabilities, "MOUNTED_TYPES", [SimpleNamespace(type="agent", manager=lambda: manager)])
    monkeypatch.setattr(type(extension_manager), "capability_revision", property(lambda self: revision))
    proc = SimpleNamespace(pid="child", exit_status=SimpleNamespace(value="done"), artifacts={})
    kernel = SimpleNamespace(dispatch=AsyncMock(return_value=proc),
                             wait=AsyncMock(return_value=SimpleNamespace(message="A runnable design")))
    router = CapabilityRouter(include_agents=builder.include_agents, kernel=kernel)
    ctx = SimpleNamespace(id="builder", extra={"task_manifest": {"evolution": {
        "required_modules": ["agent"],
    }}, "task_state": {"private": "parent work"}})
    _, routes = await router.schemas(builder, ctx)
    assert routes == {}  # No preloaded worker; the caller is excluded from its own roster.
    registered[worker.name] = SimpleNamespace(name=worker.name, description="Design an expedition")
    revision += 1  # Registration refreshes the shared capability catalog.
    _, routes = await router.schemas(builder, ctx)
    assert routes["expedition_designer"] == ("agent", "expedition_designer")
    call = ActionCall(id="dispatch-design", name=worker.name, args={"task": "Design from the supplied packet."})
    result = await router.invoke(call, agent=builder, ctx=ctx, routing=routes)
    assert result.ok and "A runnable design" in result.output
    kernel.dispatch.assert_awaited_once_with(worker.name, call.args, parent=builder, ctx=ctx)
    kernel.wait.assert_awaited_once_with(proc)
    child = child_context(worker, {"task": "Design from the supplied packet."}, builder, ctx)
    assert "task_state" not in child.extra and "task_manifest" not in child.extra
