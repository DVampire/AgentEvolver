"""Launch a single Website Builder that builds, browses, critiques and evolves."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ROOT = ROOT / "examples" / "tasks" / "website_evolution"
DEFAULT_SCENARIO_DIR = SCENARIO_ROOT / "arkbound_game"
DEFAULT_CONFIG = ROOT / "configs" / "website_evolution_demo.py"
OPTIMIZATION_CYCLES = 5
DEFAULT_BUILDER_MODEL = "llm_hub/claude-fable-5-1"


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--scenario-dir", default=str(DEFAULT_SCENARIO_DIR))
    parser.add_argument("--site-brief", help="Override <scenario-dir>/scenario.html.")
    parser.add_argument("--model", "--builder-model", dest="model", help="Builder model; must accept images.")
    parser.add_argument("--plan-mode", choices=["off", "auto", "plan"], default="auto")
    parser.add_argument("--no-monitor", action="store_true", help="Deprecated; the shared launcher always registers the run on gateway 9876.")
    parser.add_argument("--monitor-port", type=int, default=8766)
    parser.add_argument("--cfg-options", nargs="+", default=[], metavar="KEY=VALUE")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def _existing_file(raw, role):
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{role} file not found: {path}")
    return path


def resolve_inputs(args):
    config = _existing_file(args.config, "config")
    brief = _existing_file(args.site_brief or str(Path(args.scenario_dir) / "scenario.html"), "site brief")
    return config, brief


DEMO_EVOLUTION_BRIEF = """
This demo has two required outcomes: a useful next-generation website shaped
by the Builder through actual browser use, and a verified agent-system capability improvement.
You are the only agent. Build an initial polished experience, then alternate browser use,
creative product critique and implementation. Do not create users, personas, subscribers,
reviewer agents or imaginary conversations. In the browser, pursue a concrete visitor goal
through visible UI without consulting source to infer success. Record actions, outcomes,
delight, confusion and an unmet opportunity as self-observations, never real user feedback.
Propose a vivid next-generation interaction grounded in that experience: its input,
transformation, visible result, value and a small faithful trial. Explore unexpected media,
personal material, real-world information or new participation when useful. Do not restrict
ideas to current controls or cosmetic polish. Personalization must be explicit and reversible;
without an actual user's preferences, treat design choices as hypotheses to test.

After the first browser experience pass, invoke self_evolving_skill and inspect relevant existing
capabilities. Translate the chosen experience into its essential input, transformation,
observable result and explicit quality requirement. Probe that operation with existing
capabilities before registering a candidate. Diagnose what is missing or cannot meet the
required quality, reliability or cost, using the actual browser observation and baseline call IDs.
A missing website feature does not establish a missing agent capability. Available Bash
does not establish a reliable reusable method either. If the baseline already meets the
need, implement normally and investigate another unresolved need found during browser use.
Do not disguise a product edit as a new capability. Do not wait for a bug, manufacture
failure, add a cosmetic substitute or lower the stated requirement to avoid the gap.
Turn a demonstrated opportunity into a separate reusable capability candidate, register it
with adoption_tool, and evaluate the exact returned version against the preserved baseline
and an independent reuse/regression case using real tool-call evidence and measured cost.
Record keep only after passing evaluation; otherwise record and perform rollback/unload.
Use a kept candidate on a subsequent real product operation and verify the consumer result;
registration alone does not update a running Agent. Budget this before the final release.
The evaluated report must include capability_gap with user_need, required_operation,
limitation, acceptance_criterion, observation_evidence_ids and baseline_evidence_ids; the
observation and baseline calls must precede registration. Tag comparison and independent
cases with kind=comparison and kind=reuse or regression. After keep, invoke the adopted
capability synchronously on real product work and call adoption_tool action record_use.
Its report names module, name, exact version, consumer_call_id, evidence_ids and outcome.
For a skill, invoke it and execute its method: cite the skill call and subsequent product
operation/check calls. Use an auditable callable consumer; an instance-only change without
instrumented execution remains unverified. Task completion checks these runtime receipts,
including text-only endings; missing evidence yields an unsuccessful result. Record the
decision and use promptly; preserve detailed artifacts in the work record.
Never count website source, a product feature, a task report or an untested prompt as evolution.
If a prerequisite prevents the experiment, report the concrete blocker and the unfulfilled
system-evolution outcome; six releases alone do not complete this demo. Do not fabricate a gap
or keep a failing candidate to claim success. A rejected experiment is a trigger exercised,
not a verified capability improvement; report those outcomes separately.

Before done_tool, provide separate product and system evidence: the original browser experience,
proposed idea, implemented trial and repeated-journey comparison; then baseline/candidate version,
executed comparison and reuse evidence, adoption decision and actual consumer use (or blocker).
""".strip()


def build_task_text(site_brief: Path) -> str:
    from agentevolver.task.context import load_task_document, render_manifest

    return render_manifest(load_task_document(str(site_brief)).content,
        "This manifest configures the Agent experiment, not website features.\n\n" + DEMO_EVOLUTION_BRIEF,
        {
            "attachments": [{"id": "site_brief", "role": "requirements"}],
            "subscribers": [],
            "deployment": {"required_releases": OPTIMIZATION_CYCLES + 1, "topic": "deployment.ready"},
            "run_policy": {"self_review": True},
            "evolution": {"require_verified_improvement": True},
        })


def config_options(args):
    options = list(args.cfg_options)
    if args.model:
        options[:0] = [f"model_name={args.model}", f"website_builder_agent.model_name={args.model}"]
    return options


def validate_local_artifacts(config_path, site_brief, options=()):
    """Check the actual single-agent assembly, including command-line overrides."""
    from mmengine import DictAction
    import agentevolver.agent  # noqa: F401
    from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent
    from agentevolver.config import config, validate_assembly
    from agentevolver.model.config import llm_hub_models
    from agentevolver.prompt.types import parse_prompt_file
    from agentevolver.task.context import bind_manifest

    parser = argparse.ArgumentParser()
    parser.add_argument("--cfg-options", nargs="+", action=DictAction, default={})
    overrides = parser.parse_args(["--cfg-options", *options] if options else []).cfg_options
    config.initialize(config_path=str(config_path),
        args=argparse.Namespace(config=str(config_path), cfg_options=overrides), verbose=False)
    problems = list(validate_assembly(config))
    if problems:
        raise ValueError("config assembly errors: " + "; ".join(problems))
    if list(config.agent_names) != ["website_builder_agent"]:
        raise ValueError("The demo must run only website_builder_agent")
    builder = WebsiteBuilderAgent(**dict(config.website_builder_agent))
    if builder.include_agents or not builder.enable_evolving:
        raise ValueError("Builder must evolve locally with child agents disabled")
    if set(builder.env_names) != {"job", "browser_environment"}:
        raise ValueError("Builder must mount job and browser_environment")
    if set(builder.capability_allowlists.get("environment", [])) != set(builder.env_names):
        raise ValueError("Builder's browser environment must be callable")
    if not builder.use_memory or not builder.compact_input_tokens or not builder.fold_at_pressure:
        raise ValueError("Builder needs bounded conversation history")
    if config.optimization_cycles != OPTIMIZATION_CYCLES:
        raise ValueError("Expected five optimization cycles after the first release")
    if set(config.tool_names) != {"bash_tool", "apply_patch_tool", "inspect_tool", "deploy_tool", "done_tool", "adoption_tool"}:
        raise ValueError("Invalid single-builder tool roster")
    if set(config.skill_names) != {"frontend_ui_engineering_skill", "webapp_testing_skill", "self_evolving_skill"}:
        raise ValueError("Builder needs the UI, browser testing and evolution methods")
    prompt = parse_prompt_file(str(ROOT / "agentevolver/prompt/default/website_builder_agent.html"))
    if not prompt.system_template or not prompt.user_template:
        raise ValueError("Invalid builder prompt")
    catalog = llm_hub_models(max_tokens=1, default_temperature=0.0, default_timeout=1.0)
    models = {entry["model_name"]: entry for group in catalog.values() for entry in group}
    if models.get(builder.model_name, {}).get("supports_vision") is False:
        raise ValueError("Builder model cannot see browser screenshots")
    bind_manifest(build_task_text(site_brief), [str(site_brief)])
    print("Website evolution demo validation: OK")
    print(f"  scenario: {site_brief}")
    print(f"  single builder: {builder.model_name}; native browser; no participants")


def launch(args):
    config_path, site_brief = resolve_inputs(args)
    options = config_options(args)
    if args.validate_only:
        validate_local_artifacts(config_path, site_brief, options)
        return
    forwarded = ["run_meta_agent.py", "--config", str(config_path),
        "--agent-name", "website_builder_agent", "--task", build_task_text(site_brief),
        "--attach", str(site_brief), "--plan-mode", args.plan_mode,
        "--monitor-port", str(args.monitor_port)]
    if args.no_monitor:
        forwarded.append("--no-monitor")
    if options:
        forwarded.extend(["--cfg-options", *options])
    from examples import run_meta_agent

    previous = sys.argv
    try:
        sys.argv = forwarded
        asyncio.run(run_meta_agent.run_with_lifecycle())
    finally:
        sys.argv = previous


if __name__ == "__main__":
    launch(parse_args())
