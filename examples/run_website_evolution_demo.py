"""Launch the participatory website self-evolution demonstration.

This is intentionally a thin adapter over the generic orchestrator launcher: it validates the
domain brief and exactly three persona briefs, appends a role-only input manifest to the
general task document, then launches the dedicated ``website_builder_agent`` through the
standard Agent runtime lifecycle.

Examples
--------
Validate config, prompts, and inputs without calling a model or starting a browser::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py --validate-only

Run the bundled demonstration::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py \
        --model llm_hub/deepseek-v4-flash

Use another domain and three hidden personas::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py \
        --site-brief /abs/brief.html \
        --persona-brief /abs/p1.html /abs/p2.html /abs/p3.html
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "examples" / "inputs" / "website_evolution_demo"
DEFAULT_TASK = ROOT / "examples" / "tasks" / "website_feedback_evolution_demo.html"
DEFAULT_CONFIG = ROOT / "configs" / "website_evolution_demo.py"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a three-persona, participatory website self-evolution demo."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Demo config path.")
    parser.add_argument("--task-file", default=str(DEFAULT_TASK), help="General task HTML.")
    parser.add_argument(
        "--site-brief",
        default=str(DEFAULT_INPUT_DIR / "site_brief.html"),
        help="Domain-specific website brief passed to the Website Builder.",
    )
    parser.add_argument(
        "--persona-brief",
        nargs=3,
        metavar=("PERSONA_1", "PERSONA_2", "PERSONA_3"),
        default=[str(DEFAULT_INPUT_DIR / f"persona_{index:02d}.html") for index in range(1, 4)],
        help="Exactly three persona files, routed one-to-one to the three browser co-designers.",
    )
    parser.add_argument(
        "--plan-mode",
        choices=["off", "auto", "plan"],
        default="off",
        help="Forwarded to run_meta_agent.py (default: off for an unattended demo).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the configured model route for every demo agent (for example llm_hub/deepseek-v4-flash).",
    )
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        default=[],
        metavar="KEY=VALUE",
        help="Config overrides forwarded verbatim; place this option last.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse and cross-check local artifacts without initializing models/environments.",
    )
    return parser.parse_args(argv)


def _existing_file(raw: str, role: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{role} file not found: {path}")
    return path


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, list[Path]]:
    config_path = _existing_file(args.config, "config")
    task_path = _existing_file(args.task_file, "task")
    site_brief = _existing_file(args.site_brief, "site brief")
    personas = [
        _existing_file(path, f"persona {index}")
        for index, path in enumerate(args.persona_brief, start=1)
    ]
    names = [site_brief.name, *(path.name for path in personas)]
    if len(names) != len(set(names)):
        raise ValueError(
            "site/persona attachment basenames must be unique so the blind role manifest "
            "can route them without exposing their contents"
        )
    return config_path, task_path, site_brief, personas


def build_task_text(task_path: Path, site_brief: Path, personas: list[Path]) -> str:
    from agentevolver.task.loader import load_task_document

    document = load_task_document(str(task_path))
    manifest = {
        "site_brief": site_brief.name,
        "persona_01": personas[0].name,
        "persona_02": personas[1].name,
        "persona_03": personas[2].name,
        "privacy_rule": (
            "The Website Builder routes persona files by basename but never reads their "
            "contents; each file is read only by its assigned Website User Agent."
        ),
    }
    return (
        f"{document.content}\n\n"
        "## runtime-input-manifest\n"
        "This manifest assigns attachment roles; it does not reveal persona contents.\n"
        f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
    )


def validate_local_artifacts(
    config_path: Path,
    task_path: Path,
    site_brief: Path,
    personas: list[Path],
) -> None:
    """Perform side-effect-free parsing and assembly checks (no model/browser startup)."""
    from argparse import Namespace

    import inflection

    from agentevolver.config import config, validate_assembly
    from agentevolver.prompt.types import parse_prompt_file
    from agentevolver.registry import AGENT
    from agentevolver.task.loader import load_task_document

    # Importing the actor package registers all three concrete co-designer classes.
    import agentevolver.agent  # noqa: F401

    config.initialize(
        config_path=str(config_path),
        args=Namespace(config=str(config_path), cfg_options={}),
        verbose=False,
    )
    problems = list(validate_assembly(config))
    if problems:
        raise ValueError("config assembly errors:\n- " + "\n- ".join(problems))

    expected_agents = {
        "website_builder_agent",
        "website_user_1_agent",
        "website_user_2_agent",
        "website_user_3_agent",
    }
    missing_config = expected_agents.difference(config.agent_names)
    registry_agents = {
        agent_class.model_fields["name"].default: agent_class
        for agent_class in AGENT._module_dict.values()
    }
    registry_agent_names = set(registry_agents)
    missing_registry = expected_agents.difference(registry_agent_names)
    if missing_config or missing_registry:
        raise ValueError(
            f"agent wiring incomplete: missing_config={sorted(missing_config)}, "
            f"missing_registry={sorted(missing_registry)}"
        )

    builder_class = registry_agents["website_builder_agent"]
    builder_key = inflection.underscore(builder_class.__name__)
    builder = builder_class(**dict(getattr(config, builder_key)))
    if (
        builder.name != "website_builder_agent"
        or builder.prompt_name != "website_builder_agent"
        or not builder.enable_evolving
    ):
        raise ValueError(
            "website_builder_agent must resolve to the dedicated evolvable builder "
            f"class and prompt, got name={builder.name!r}, prompt={builder.prompt_name!r}, "
            f"enable_evolving={builder.enable_evolving!r}"
        )

    prompt_dir = ROOT / "agentevolver" / "prompt" / "default"
    prompts = {
        path.stem: parse_prompt_file(str(path))
        for path in (
            prompt_dir / "website_builder_agent.html",
            prompt_dir / "website_user_agent.html",
        )
    }
    for name, prompt in prompts.items():
        if prompt.name != name or not prompt.system_template or not prompt.user_template:
            raise ValueError(f"invalid prompt artifact: {name}")

    task = load_task_document(str(task_path))
    if not task.content or "## objective" not in task.content:
        raise ValueError("task HTML did not produce the expected semantic clean text")

    task_text = build_task_text(task_path, site_brief, personas)
    if "runtime-input-manifest" not in task_text:
        raise ValueError("runtime input manifest was not appended")

    required_skills = {
        "frontend_ui_engineering_skill",
        "api_and_interface_design_skill",
        "webapp_testing_skill",
        "deploy_skill",
        "self_evolving_skill",
        "generate_skill",
        "optimize_skill",
        "evaluate_skill",
    }
    missing_skills = required_skills.difference(config.skill_names)
    if missing_skills:
        raise ValueError(f"missing required skills: {sorted(missing_skills)}")
    if "publish_event_tool" not in config.tool_names:
        raise ValueError("publish_event_tool is required for release notifications")
    for agent_name in (
        "website_user_1_agent",
        "website_user_2_agent",
        "website_user_3_agent",
    ):
        agent_class = registry_agents[agent_name]
        key = inflection.underscore(agent_class.__name__)
        instance_config = dict(getattr(config, key))
        topics = list(instance_config.get("subscription_topics") or [])
        if topics != ["website.releases"]:
            raise ValueError(f"{key} must subscribe to website.releases, got {topics}")
        instance = agent_class(**instance_config)
        if (
            instance.name != agent_name
            or instance.prompt_name != "website_user_agent"
            or instance.env_name != "browser_environment"
        ):
            raise ValueError(
                f"{key} resolved to an invalid Website User Agent instance: "
                f"name={instance.name!r}, prompt={instance.prompt_name!r}, "
                f"environment={instance.env_name!r}"
            )

    print("Website evolution demo validation: OK")
    print(f"  config: {config_path}")
    print(f"  task: {task_path}")
    print(f"  site brief: {site_brief}")
    print(f"  personas: {', '.join(str(path) for path in personas)}")
    print(f"  model: {config.model_name}")


def launch(args: argparse.Namespace) -> None:
    config_path, task_path, site_brief, personas = resolve_inputs(args)
    if args.validate_only:
        validate_local_artifacts(config_path, task_path, site_brief, personas)
        return

    task_text = build_task_text(task_path, site_brief, personas)
    forwarded = [
        "run_meta_agent.py",
        "--config",
        str(config_path),
        "--agent-name",
        "website_builder_agent",
        "--task",
        task_text,
        "--plan-mode",
        args.plan_mode,
        "--attach",
        str(task_path),
        str(site_brief),
        *(str(path) for path in personas),
    ]
    cfg_options = list(args.cfg_options)
    if args.model:
        cfg_options.insert(0, f"model_name={args.model}")
    if cfg_options:
        forwarded.extend(["--cfg-options", *cfg_options])

    # Reuse the canonical runtime instead of maintaining a second manager lifecycle.
    from examples import run_meta_agent

    previous_argv = sys.argv
    try:
        sys.argv = forwarded
        asyncio.run(run_meta_agent.main())
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    launch(parse_args())
