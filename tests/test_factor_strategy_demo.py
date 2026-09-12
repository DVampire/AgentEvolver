"""The research demo assembles without running a model or querying market data."""
import json
import sys
from types import SimpleNamespace

import pytest
from mmengine import Config

from examples.run_factor_strategy_mining_demo import (
    ROOT, DEFAULT_CONFIG, DEFAULT_TASK_DIR, launch, parse_args, task_inputs,
)
from agentevolver.agent.actor.factor_strategy_mining_agent import FactorStrategyMiningAgent
from agentevolver.config import validate_assembly
from agentevolver.task.context import bind_manifest, parse_manifest, resolve_task


def test_one_researcher_keeps_shared_plan_and_dynamic_environment_scope():
    cfg = Config.fromfile(str(DEFAULT_CONFIG))
    assert validate_assembly(cfg) == []
    assert cfg.agent_names == ["factor_strategy_mining_agent"]
    assert cfg.connector_names == []
    assert cfg.env_names == ["job", "browser_environment"]
    agent = FactorStrategyMiningAgent(**cfg.factor_strategy_mining_agent)
    assert agent.use_plan and agent.use_memory and agent.enable_evolving
    assert not agent.include_agents and agent.capability_allowlists["agent"] == []
    assert agent.accepts_evolved == ["environment"]
    assert "connector" not in agent.capability_allowlists
    assert agent.model_name == "llm_hub/gpt-6-astra"
    assert agent.compact_input_tokens == 100_000


def test_task_and_study_are_staged_with_runtime_policy_from_config(tmp_path):
    inputs = task_inputs(DEFAULT_TASK_DIR)
    cfg = Config.fromfile(str(DEFAULT_CONFIG))
    text, files, metadata = resolve_task(
        SimpleNamespace(task_file=str(inputs[0]), attach=[str(inputs[1])]), str(tmp_path),
        manifest_defaults=cfg.task_manifest_defaults,
    )
    assert files == list(map(str, inputs))
    manifest = parse_manifest(text)[2]
    assert manifest["evolution"]["required_module_counts"] == {"connector": 1, "environment": 2}
    assert manifest["subscribers"] == []
    assert manifest["research"]["holdout_control"] == "protocol_only"
    # Configuration is applied to runtime input, never written into the product document/view.
    from pathlib import Path
    assert "runtime-input-manifest" not in Path(metadata["task_view"]).read_text()
    staged = [f"/session/inputs/{i}_{path.name}" for i, path in enumerate(inputs)]
    bound = bind_manifest(text, staged)[2]
    assert bound["attachments"][0]["path"] == staged[0]
    assert bound["attachments"][1]["path"] == staged[1]
    assert "minimum_net_cagr" not in text  # numerical protocol is read from the attachment
    assert "record_use" not in text  # lifecycle instructions stay in the prompt/skill
    study = json.loads(inputs[1].read_text())
    splits = study["splits"]
    assert splits["train"][1] < splits["validation"][0] < splits["validation"][1] < splits["test"][0]
    assert splits["final_test_attempts"] == 1
    assert "holdout_control" not in splits


def test_prompt_modules_and_domain_skill_can_be_loaded(tmp_path):
    from agentevolver.prompt.types import parse_prompt_file
    from agentevolver.skill.context import SkillContextManager

    prompt = parse_prompt_file(str(ROOT / "agentevolver/prompt/default/factor_strategy_mining_agent.html"))
    assert prompt.system_template and prompt.user_template
    folder = ROOT / "agentevolver/skill/finance/factor_strategy_research_skill"
    manager = SkillContextManager(base_dir=str(tmp_path))
    skill = manager._parse_skill_dir(folder)
    assert skill.name == "factor_strategy_research_skill"
    assert len(skill.references) == 4


@pytest.mark.asyncio
async def test_registered_environments_expand_the_researcher_scope_without_children(monkeypatch):
    from agentevolver.agent.context import capabilities
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.extension import extension_manager
    from agentevolver.extension.types import Manifest, ManifestComponent

    manifest = Manifest(components=[])
    monkeypatch.setattr(type(extension_manager), "read_manifest", lambda self: manifest)
    # Existing routing tests cover manager transport. Here exercise actual grant expansion
    # on this actor's assembled configuration, without starting environment processes.
    captured = []

    async def assemble(agent, ctx, *, include_agents):
        captured.append((list(ctx.extra["environment_allowlist"]), include_agents))
        return [], {}

    monkeypatch.setattr(capabilities, "assemble_native_tools", assemble)
    cfg = Config.fromfile(str(DEFAULT_CONFIG))
    agent = FactorStrategyMiningAgent(**cfg.factor_strategy_mining_agent)
    router = CapabilityRouter(include_agents=agent.include_agents)
    ctx = SimpleNamespace(extra={})
    await router.schemas(agent, ctx)
    assert captured[-1] == (["job", "browser_environment"], False)
    for name in ("factors", "strategies"):
        manifest.components.append(ManifestComponent(
            module="environment", name=name, version="1.0.0", file=f"environment/{name}",
        ))
    await router.schemas(agent, ctx)
    assert captured[-1] == (["job", "browser_environment", "factors", "strategies"], False)
    assert ctx.extra["agent_allowlist"] == []


def test_launcher_uses_shared_lifecycle_and_config_overrides(monkeypatch, tmp_path):
    from examples import run_meta_agent

    forwarded = []

    async def record_launch():
        forwarded.extend(sys.argv)
        args = run_meta_agent.parse_args()
        cfg = Config.fromfile(args.config)
        cfg.merge_from_dict(args.cfg_options)
        text, files, _ = resolve_task(args, str(tmp_path), manifest_defaults=cfg.task_manifest_defaults)
        assert parse_manifest(text)[2]["evolution"]["required_module_counts"]["environment"] == 3
        assert len(files) == 2

    original = sys.argv
    monkeypatch.setattr(run_meta_agent, "run_with_lifecycle", record_launch)
    launch(parse_args(["--model", "llm_hub/gpt-6-astra", "--cfg-options",
                       "factor_strategy_mining_agent.max_step=20",
                       "task_manifest_defaults.evolution.required_module_counts.environment=3"]))
    assert sys.argv is original
    assert forwarded[forwarded.index("--agent-name") + 1] == "factor_strategy_mining_agent"
    assert "--attach" in forwarded and "--plan-mode" in forwarded
    assert "--task-file" in forwarded and "--task" not in forwarded
    assert "factor_strategy_mining_agent.max_step=20" in forwarded
    assert "factor_strategy_mining_agent.model_name=llm_hub/gpt-6-astra" in forwarded


def test_missing_input_fails_before_runtime_start(tmp_path):
    with pytest.raises(FileNotFoundError, match="task.html"):
        task_inputs(tmp_path)
