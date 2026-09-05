"""Launch the participatory website self-evolution demonstration.

This is intentionally a thin adapter over the generic orchestrator launcher: it validates one
scenario brief and exactly three persona briefs, appends the demo's attachment routing and
iteration policy to the task, then launches the general ``website_builder_agent`` through the
standard Agent runtime lifecycle. Scenario-specific counts and acceptance/co-design cadence stay here rather
than in the reusable Builder prompt.

Examples
--------
Validate config, prompts, and inputs without calling a model or starting a browser::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py --validate-only

Run the bundled ECHO Ark demonstration with its configured heterogeneous panel::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py

Use another self-contained scenario directory::

    /home/wtzhang/miniconda3/envs/agentos/bin/python \
        examples/run_website_evolution_demo.py \
        --scenario-dir /abs/my_scenario

The directory contains ``scenario.html`` and ``persona_01.html`` through
``persona_03.html``. Individual files can still be overridden explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ROOT = ROOT / "examples" / "tasks" / "website_evolution"
DEFAULT_SCENARIO_DIR = SCENARIO_ROOT / "echo_ark"
DEFAULT_CONFIG = ROOT / "configs" / "website_evolution_demo.py"
OPTIMIZATION_CYCLES = 5
INITIAL_STEP_BUDGET = 36
ITERATION_STEP_BUDGET = 30
DEFAULT_USER_MODELS = [
    "llm_hub/gpt-6-astra",
    "llm_hub/claude-fable-5-1",
    "llm_hub/deepseek-v4-flash-vision-exp",
]
DEFAULT_ACCEPTANCE_MODEL = "llm_hub/gpt-5.6-sol"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a three-persona, participatory website self-evolution demo."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Demo config path.")
    parser.add_argument("--no-monitor", action="store_true", help="Disable the generic run dashboard.")
    parser.add_argument("--monitor-port", type=int, default=8766, help="Internal monitor port; public pages share gateway port 9876.")
    parser.add_argument(
        "--scenario-dir",
        default=str(DEFAULT_SCENARIO_DIR),
        help=("Self-contained scenario directory with scenario.html and three persona files."),
    )
    parser.add_argument(
        "--site-brief",
        default=None,
        help="Override <scenario-dir>/scenario.html.",
    )
    parser.add_argument(
        "--persona-brief",
        nargs=3,
        metavar=("PERSONA_1", "PERSONA_2", "PERSONA_3"),
        default=None,
        help=(
            "Override the three persona files from scenario-dir; routed one-to-one "
            "to the browser co-designers."
        ),
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
        "--acceptance-model",
        default=None,
        help="Override the independent Browser Agent used for deployed release acceptance.",
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


def _existing_directory(raw: str, role: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"{role} directory not found: {path}")
    return path


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, list[Path]]:
    config_path = _existing_file(args.config, "config")
    scenario_dir = _existing_directory(args.scenario_dir, "scenario")
    site_brief = _existing_file(
        args.site_brief or str(scenario_dir / "scenario.html"),
        "site brief",
    )
    persona_briefs = args.persona_brief or [
        str(scenario_dir / f"persona_{index:02d}.html") for index in range(1, 4)
    ]
    personas = [
        _existing_file(path, f"persona {index}")
        for index, path in enumerate(persona_briefs, start=1)
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
    acceptance_model: str = DEFAULT_ACCEPTANCE_MODEL,
) -> str:
    from agentevolver.task.context import load_task_document

    document = load_task_document(str(site_brief))
    models = list(user_models or DEFAULT_USER_MODELS)
    manifest = {
        "attachments": [
            {
                "id": "site_brief",
                "role": "requirements",
            },
            *[
                {
                    "id": f"persona_{index:02d}",
                    "role": "user_context",
                }
                for index, _path in enumerate(personas, start=1)
            ],
        ],
        "optimization_cycles": OPTIMIZATION_CYCLES,
        "initial_step_budget": INITIAL_STEP_BUDGET,
        "iteration_step_budget": ITERATION_STEP_BUDGET,
        "participants": [
            {
                "id": f"participant_{index:02d}",
                "user_context_attachment": f"persona_{index:02d}",
                "model": model,
            }
            for index, model in enumerate(models, start=1)
        ],
        "release_acceptance": {
            "agent": "browser_agent",
            "model": acceptance_model,
            "after_initial_build": True,
            "after_each_optimization": True,
            "exact_deployed_url_only": True,
            "independent_from_user_codesign": True,
        },
        "codesign_policy": {
            "participants_are_evaluators": False,
            "continue_participant_identity": True,
            "fresh_browser_each_conversation_turn": True,
        },
        "run_policy": {
            "blind_initial_build": True,
        },
        "privacy_rule": (
            "Runtime privately routes each persona attachment to exactly one Website User "
            "Agent. The Website Builder receives participant and job identifiers, never a "
            "persona path or its contents."
        ),
    }
    return (
        f"{document.content}\n\n"
        "## runtime-input-manifest\n"
        "This manifest configures the Agent experiment, not website features. Participant feedback "
        "and clarification travel through Agent results/messages; no in-product developer chat, "
        "request inbox, or approval UI is implied. It assigns attachment roles without revealing "
        "persona contents.\n"
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
    from agentevolver.task.context import load_task_document

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
        "browser_agent",
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
            prompt_dir / "browser_agent.html",
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
    # What matters is that every long-running role shares ONE policy and that none of
    # them silently disables compaction — not which numbers the shared policy holds.
    # Pinning the literals here meant tuning the fold threshold for cost broke the
    # launcher instead of the config, which is the wrong file to have to edit.
    policy_keys = (
        "retain_recent_steps",
        "compact_after_steps",
        "compact_body_tokens",
        "fold_at_pressure",
    )
    context_roles = (
        "website_builder_agent",
        "generate_agent",
        "optimize_agent",
        "evaluate_agent",
        "website_user_agent",
    )
    policies = {}
    for role in context_roles:
        role_config = dict(getattr(config, role))
        policy = {key: role_config.get(key) for key in policy_keys}
        if any(value is None for value in policy.values()):
            raise ValueError(f"{role} does not declare a bounded-history policy: {policy}")
        if not role_config.get("use_memory"):
            raise ValueError(f"{role} must keep use_memory=True; compaction depends on it")
        if not policy["compact_body_tokens"] or not policy["fold_at_pressure"]:
            raise ValueError(
                f"{role} disables compaction ({policy}); a long-running role that never "
                "folds grows its prefix until the provider refuses the request"
            )
        policies[role] = policy

    distinct = {tuple(sorted(policy.items())) for policy in policies.values()}
    if len(distinct) > 1:
        raise ValueError(
            "every long-running role must share one bounded-history policy, got "
            + "; ".join(f"{role}={policy}" for role, policy in sorted(policies.items()))
        )

    browser_config = dict(getattr(config, "browser_agent"))
    if browser_config.get("use_memory") or browser_config.get("env_name") != "browser_environment":
        raise ValueError(
            "browser_agent acceptance must be stateless and browser-only: "
            f"use_memory={browser_config.get('use_memory')!r}, "
            f"environment={browser_config.get('env_name')!r}"
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
        "inspect_tool",
        "deploy_tool",
        "done_tool",
        "send_message_tool",
        "adoption_tool",
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
    # Browser-only isolation, as the agent actually declares it. Two fields carry it:
    # `capability_allowlists` bounds what the router will project, and `env_names` is the
    # only environment it may mount. Checked here rather than trusted, because a visitor
    # that could reach the workspace would be co-designing with the builder's own files
    # instead of with the deployed site.
    expected_user_allowlists = {
        "tool": ["done_tool"],
        "skill": [],
        "connector": [],
        "plugin": [],
        "workflow": [],
    }
    expected_user_envs = ["browser_environment"]

    def check_browser_only(agent, label: str) -> None:
        if dict(agent.capability_allowlists) != expected_user_allowlists:
            raise ValueError(
                f"{label} must remain browser-only: expected={expected_user_allowlists}, "
                f"actual={dict(agent.capability_allowlists)}"
            )
        if list(agent.env_names) != expected_user_envs:
            raise ValueError(
                f"{label} must mount only {expected_user_envs}, not {list(agent.env_names)}"
            )

    check_browser_only(instance, "website_user_agent")

    acceptance_class = registry_agents["browser_agent"]
    acceptance_key = inflection.underscore(acceptance_class.__name__)
    acceptance = acceptance_class(**dict(getattr(config, acceptance_key)))
    check_browser_only(acceptance, "browser_agent acceptance")

    expected_models = {
        "website_builder_agent": "llm_hub/gpt-6-astra",
        "browser_agent": DEFAULT_ACCEPTANCE_MODEL,
        "website_user_agent": DEFAULT_USER_MODELS[0],
    }
    actual_models = {key: str(getattr(config, key).get("model_name")) for key in expected_models}
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

    # Every browser-driving role needs a route that accepts images. The browser
    # environment attaches a screenshot to each observation, so a text-only route does
    # not degrade — it returns 400 "Model do not support image input" on EVERY call and
    # the participant contributes nothing to the round. `deepseek-v4-flash` sat in the
    # panel exactly that way: three retries per step, no feedback, and the loop reading
    # as a model that would not cooperate rather than as a route that cannot see.
    from agentevolver.model.config import llm_hub_models

    # Only `model_name` and `supports_vision` are read, so the sizing arguments are
    # placeholders: this stays a local check that initializes no client.
    catalog = llm_hub_models(
        max_tokens=1, default_temperature=0.0, default_timeout=1.0,
    )
    blind = {
        entry["model_name"]
        for group in catalog.values()
        for entry in group
        if isinstance(entry, dict) and entry.get("supports_vision") is False
    }
    browser_routes = {
        "browser_agent (acceptance)": [str(getattr(config, "browser_agent").get("model_name"))],
        "website_user_models (the panel)": [str(m) for m in config.website_user_models],
    }
    sightless = {
        role: [route for route in routes if route in blind]
        for role, routes in browser_routes.items()
    }
    offenders = {role: routes for role, routes in sightless.items() if routes}
    if offenders:
        raise ValueError(
            "a browser-driving role is routed to a model that cannot accept images; "
            "the browser environment sends a screenshot every observation, so every "
            f"call would be refused: {offenders}"
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
    acceptance_model = args.acceptance_model or DEFAULT_ACCEPTANCE_MODEL
    if args.model:
        acceptance_model = args.model
    task_text = build_task_text(
        site_brief,
        personas,
        user_models,
        acceptance_model=acceptance_model,
    )
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
    forwarded.extend(["--monitor-port", str(args.monitor_port)])
    if args.no_monitor:
        forwarded.append("--no-monitor")
    if args.model:
        cfg_options[:0] = [
            f"model_name={args.model}",
            f"website_builder_agent.model_name={args.model}",
            f"browser_agent.model_name={args.model}",
            f"generate_agent.model_name={args.model}",
            f"optimize_agent.model_name={args.model}",
            f"evaluate_agent.model_name={args.model}",
            f"website_user_agent.model_name={args.model}",
        ]
    else:
        if args.builder_model:
            cfg_options.insert(0, f"website_builder_agent.model_name={args.builder_model}")
        if args.acceptance_model:
            cfg_options.insert(0, f"browser_agent.model_name={args.acceptance_model}")
    if cfg_options:
        forwarded.extend(["--cfg-options", *cfg_options])

    # Reuse the canonical runtime instead of maintaining a second manager lifecycle.
    from examples import run_meta_agent

    previous_argv = sys.argv
    try:
        sys.argv = forwarded
        asyncio.run(run_meta_agent.run_with_lifecycle())
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    launch(parse_args())
