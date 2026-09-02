"""Launch the participatory website self-evolution demonstration.

This is intentionally a thin adapter over the generic orchestrator launcher: it validates one
scenario brief and exactly three persona briefs, appends the demo's attachment routing and
iteration policy to the task, then launches the general ``website_builder_agent`` through the
standard Agent runtime lifecycle. Scenario-specific counts and evaluation cadence stay here rather
than in the reusable Builder prompt.

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
    parser.add_argument(
        "--site-brief",
        default=str(DEFAULT_SITE_BRIEF),
        help="Domain-specific scenario task passed to the Website Builder.",
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


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, list[Path]]:
    config_path = _existing_file(args.config, "config")
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
    return config_path, site_brief, personas


def build_task_text(
    site_brief: Path,
    personas: list[Path],
    user_models: Sequence[str] | None = None,
) -> str:
    from agentevolver.task.loader import load_task_document

    document = load_task_document(str(site_brief))
    models = list(user_models or DEFAULT_USER_MODELS)
    manifest = {
        "attachments": [
            {
                "id": "site_brief",
                "role": "requirements",
                "source_path": str(site_brief),
            },
            *[
                {
                    "id": f"persona_{index:02d}",
                    "role": "user_context",
                    "source_path": str(path),
                }
                for index, path in enumerate(personas, start=1)
            ],
        ],
        "optimization_cycles": OPTIMIZATION_CYCLES,
        "participants": [
            {
                "id": f"participant_{index:02d}",
                "user_context_attachment": f"persona_{index:02d}",
                "model": model,
            }
            for index, model in enumerate(models, start=1)
        ],
        "run_policy": {
            "blind_initial_build": True,
            "evaluate_initial_build": True,
            "evaluate_after_each_optimization": True,
            "continue_participant_identity": True,
            "fresh_browser_each_evaluation": True,
            "evolve_only_proven_reusable_capability_gaps": True,
        },
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
    site_brief: Path,
    personas: list[Path],
) -> None:
    """Perform side-effect-free parsing and assembly checks (no model/browser startup)."""
    from argparse import Namespace

    import inflection

    # Importing the actor package registers the reusable co-designer template.
    import agentevolver.agent  # noqa: F401
    from agentevolver.config import config, validate_assembly
    from agentevolver.prompt.types import parse_prompt_file
    from agentevolver.registry import AGENT
    from agentevolver.task.loader import load_task_document

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
        "generate_agent",
        "optimize_agent",
        "evaluate_agent",
        "website_user_agent",
    }
    actual_agents = set(config.agent_names)
    registry_agents = {
        agent_class.model_fields["name"].default: agent_class
        for agent_class in AGENT._module_dict.values()
    }
    registry_agent_names = set(registry_agents)
    missing_registry = expected_agents.difference(registry_agent_names)
    if actual_agents != expected_agents or missing_registry:
        raise ValueError(
            f"agent wiring must be minimal: expected={sorted(expected_agents)}, "
            f"actual={sorted(actual_agents)}, "
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

    scenario = load_task_document(str(site_brief))
    if not scenario.content:
        raise ValueError("scenario HTML did not produce semantic task text")
    if int(config.get("optimization_cycles", 0)) != OPTIMIZATION_CYCLES:
        raise ValueError(
            "website evolution config must require exactly five optimization cycles "
            f"after the initial build, got {config.get('optimization_cycles')!r}"
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

    task_text = build_task_text(site_brief, personas)
    if "runtime-input-manifest" not in task_text:
        raise ValueError("runtime input manifest was not appended")

    required_skills = {
        "frontend_ui_engineering_skill",
        "webapp_testing_skill",
        "self_evolving_skill",
        "generate_skill",
        "optimize_skill",
        "evaluate_skill",
    }
    actual_skills = set(config.skill_names)
    if actual_skills != required_skills:
        raise ValueError(
            "website evolution must mount only non-overlapping methods: "
            f"expected={sorted(required_skills)}, actual={sorted(actual_skills)}"
        )
    expected_tools = {
        "bash_tool",
        "apply_patch_tool",
        "deploy_tool",
        "done_tool",
        "send_message_tool",
        "evolution_tool",
    }
    actual_tools = set(config.tool_names)
    if actual_tools != expected_tools:
        raise ValueError(
            "website evolution uses the minimal workspace/deploy/continuation tool set: "
            f"expected={sorted(expected_tools)}, actual={sorted(actual_tools)}"
        )
    agent_class = registry_agents["website_user_agent"]
    key = inflection.underscore(agent_class.__name__)
    instance_config = dict(getattr(config, key))
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
    expected_user_allowlists = {
        "tool_allowlist": ["done_tool"],
        "skill_allowlist": [],
        "connector_allowlist": [],
        "plugin_allowlist": [],
        "environment_allowlist": ["browser_environment"],
        "workflow_allowlist": [],
    }
    actual_user_allowlists = instance._required_capability_allowlists()
    if actual_user_allowlists != expected_user_allowlists:
        raise ValueError(
            "website_user_agent must remain browser-only: "
            f"expected={expected_user_allowlists}, actual={actual_user_allowlists}"
        )

    expected_models = {
        "website_builder_agent": "llm_hub/claude-opus-5",
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
    print(f"  scenario task: {site_brief}")
    print(f"  personas: {', '.join(str(path) for path in personas)}")
    print("  role models:")
    for role, model in actual_models.items():
        print(f"    {role}: {model}")


def launch(args: argparse.Namespace) -> None:
    config_path, site_brief, personas = resolve_inputs(args)
    if args.validate_only:
        validate_local_artifacts(config_path, site_brief, personas)
        return

    user_models = list(args.user_model or DEFAULT_USER_MODELS)
    if args.model:
        user_models = [args.model] * 3
    task_text = build_task_text(site_brief, personas, user_models)
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
        str(site_brief),
        *(str(path) for path in personas),
    ]
    cfg_options = list(args.cfg_options)
    if args.model:
        cfg_options[:0] = [
            f"model_name={args.model}",
            f"website_builder_agent.model_name={args.model}",
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
