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

Run the bundled ECHO Ark demonstration with its configured heterogeneous panel::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py

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
DEFAULT_ECHO_INPUT_DIR = DEFAULT_INPUT_DIR / "echo_ark"
DEFAULT_TASK = ROOT / "examples" / "tasks" / "website_feedback_evolution_demo.html"
DEFAULT_SITE_BRIEF = (
    ROOT / "examples" / "tasks" / "website_evolution_scenarios" / "echo_ark.html"
)
DEFAULT_CONFIG = ROOT / "configs" / "website_evolution_demo.py"
OPTIMIZATION_CYCLES = 5
DEFAULT_USER_MODELS = [
    "llm_hub/claude-opus-5",
    "llm_hub/gpt-5.6-sol",
    "llm_hub/deepseek-v4-flash",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a three-persona, participatory website self-evolution demo."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Demo config path.")
    parser.add_argument("--task-file", default=str(DEFAULT_TASK), help="General task HTML.")
    parser.add_argument(
        "--site-brief",
        default=str(DEFAULT_SITE_BRIEF),
        help="Domain-specific website brief passed to the Website Builder.",
    )
    parser.add_argument(
        "--persona-brief",
        nargs=3,
        metavar=("PERSONA_1", "PERSONA_2", "PERSONA_3"),
        default=[
            str(DEFAULT_ECHO_INPUT_DIR / f"persona_{index:02d}.html")
            for index in range(1, 4)
        ],
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
        help=(
            "Override every demo agent with one model (compatibility/debug option). "
            "By default the role-specific models in the config are preserved."
        ),
    )
    parser.add_argument(
        "--builder-model",
        default=None,
        help="Override only the Website Builder model route.",
    )
    parser.add_argument(
        "--code-model",
        default=None,
        help="Override only the Code Agent model route.",
    )
    parser.add_argument(
        "--user-model",
        nargs=3,
        metavar=("USER_1", "USER_2", "USER_3"),
        default=None,
        help="Override the three Website User Agent model routes in persona order.",
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


def build_task_text(
    task_path: Path,
    site_brief: Path,
    personas: list[Path],
    user_models: Sequence[str] | None = None,
) -> str:
    from agentevolver.task.loader import load_task_document

    document = load_task_document(str(task_path))
    manifest = {
        "site_brief": str(site_brief),
        "persona_01": str(personas[0]),
        "persona_02": str(personas[1]),
        "persona_03": str(personas[2]),
        "optimization_cycles": OPTIMIZATION_CYCLES,
        "required_versions": [f"V{index}" for index in range(OPTIMIZATION_CYCLES + 1)],
        "user_models": list(user_models or DEFAULT_USER_MODELS),
        "privacy_rule": (
            "The Website Builder routes each exact persona path but never reads its contents; "
            "each file is read only by its assigned Website User Agent."
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

    # Importing the actor package registers the reusable co-designer template.
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
        "website_user_agent",
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
    if int(config.get("optimization_cycles", 0)) != OPTIMIZATION_CYCLES:
        raise ValueError(
            "website evolution config must require exactly five optimization cycles "
            f"after V0, got {config.get('optimization_cycles')!r}"
        )

    # All long-running roles use the same bounded-history protocol proven by the
    # SWE-bench MetaAgent.  A role-specific model may choose native or portable
    # compaction, but no role may silently disable compaction altogether.
    expected_context_policy = {
        "retain_recent_steps": 4,
        "compact_after_steps": 18,
        "compact_body_tokens": 100000,
        "fold_at_pressure": 0.85,
    }
    context_roles = (
        "website_builder_agent",
        "code_agent",
        "general_agent",
        "reviewer_agent",
        "generate_agent",
        "optimize_agent",
        "evaluate_agent",
        "website_user_agent",
    )
    for role in context_roles:
        role_config = dict(getattr(config, role))
        actual_policy = {
            key: role_config.get(key) for key in expected_context_policy
        }
        if actual_policy != expected_context_policy or not role_config.get("use_memory"):
            raise ValueError(
                f"{role} must use the SWE-bench bounded-history policy: "
                f"expected={expected_context_policy}, actual={actual_policy}, "
                f"use_memory={role_config.get('use_memory')!r}"
            )

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
    agent_class = registry_agents["website_user_agent"]
    key = inflection.underscore(agent_class.__name__)
    instance_config = dict(getattr(config, key))
    topics = list(instance_config.get("subscription_topics") or [])
    if topics != ["website.releases"]:
        raise ValueError(f"{key} must subscribe to website.releases, got {topics}")
    instance = agent_class(**instance_config)
    if (
        instance.name != "website_user_agent"
        or instance.prompt_name != "website_user_agent"
        or instance.env_name != "browser_environment"
    ):
        raise ValueError(
            f"{key} resolved to an invalid Website User Agent template: "
            f"name={instance.name!r}, prompt={instance.prompt_name!r}, "
            f"environment={instance.env_name!r}"
        )

    expected_models = {
        "website_builder_agent": "llm_hub/claude-opus-5",
        "code_agent": "llm_hub/claude-opus-5",
        "website_user_agent": "llm_hub/claude-opus-5",
    }
    actual_models = {
        key: str(getattr(config, key).get("model_name")) for key in expected_models
    }
    if actual_models != expected_models:
        raise ValueError(
            "default role model routing is invalid: "
            f"expected={expected_models}, actual={actual_models}"
        )
    if list(config.website_user_models) != DEFAULT_USER_MODELS:
        raise ValueError(
            "default per-dispatch user model routes are invalid: "
            f"{list(config.website_user_models)!r}"
        )

    print("Website evolution demo validation: OK")
    print(f"  config: {config_path}")
    print(f"  task: {task_path}")
    print(f"  site brief: {site_brief}")
    print(f"  personas: {', '.join(str(path) for path in personas)}")
    print("  role models:")
    for role, model in actual_models.items():
        print(f"    {role}: {model}")


def launch(args: argparse.Namespace) -> None:
    config_path, task_path, site_brief, personas = resolve_inputs(args)
    if args.validate_only:
        validate_local_artifacts(config_path, task_path, site_brief, personas)
        return

    user_models = list(args.user_model or DEFAULT_USER_MODELS)
    if args.model:
        user_models = [args.model] * 3
    task_text = build_task_text(task_path, site_brief, personas, user_models)
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
        cfg_options[:0] = [
            f"model_name={args.model}",
            f"website_builder_agent.model_name={args.model}",
            f"code_agent.model_name={args.model}",
            f"general_agent.model_name={args.model}",
            f"reviewer_agent.model_name={args.model}",
            f"generate_agent.model_name={args.model}",
            f"optimize_agent.model_name={args.model}",
            f"evaluate_agent.model_name={args.model}",
            f"website_user_agent.model_name={args.model}",
        ]
    else:
        if args.builder_model:
            cfg_options.insert(
                0, f"website_builder_agent.model_name={args.builder_model}"
            )
        if args.code_model:
            cfg_options.insert(0, f"code_agent.model_name={args.code_model}")
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
