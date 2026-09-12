"""Launch a single Website Builder that builds, browses, critiques and evolves."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ROOT = ROOT / "examples" / "tasks" / "website_evolution"
DEFAULT_SCENARIO_DIR = SCENARIO_ROOT / "arkbound_game"
DEFAULT_CONFIG = ROOT / "configs" / "website_evolution_demo.py"
OPTIMIZATION_CYCLES = 5


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Demo runtime configuration.")
    parser.add_argument("--scenario-dir", default=str(DEFAULT_SCENARIO_DIR))
    parser.add_argument("--site-brief", help="Override <scenario-dir>/scenario.html.")
    parser.add_argument("--model", "--builder-model", dest="model", help="Builder model; must accept images.")
    parser.add_argument("--plan-mode", choices=["off", "auto", "plan"], default="auto")
    parser.add_argument("--no-monitor", action="store_true", help="Deprecated; the shared launcher always registers the run on gateway 9876.")
    parser.add_argument("--monitor-port", type=int, default=8766)
    parser.add_argument("--cfg-options", nargs="+", default=[], metavar="KEY=VALUE")
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


def load_experiment(site_brief: Path) -> dict:
    """Scenario settings stay separate from the product requirements."""
    path = site_brief.parent / "experiment.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def task_inputs(site_brief: Path) -> list[Path]:
    """Stage declared material files alongside the brief using shared task preparation."""
    return [site_brief, *[
        _existing_file(site_brief.parent / path, "task material")
        for path in load_experiment(site_brief).get("materials", [])
    ]]


def build_task_text(site_brief: Path) -> str:
    from agentevolver.task.context import load_task_document, render_manifest

    return render_manifest(load_task_document(str(site_brief)).content,
        "Experiment configuration; agent behavior follows its system prompt and skills.",
        {
            "attachments": [{"id": "site_brief", "role": "requirements"}, *[
                {"id": f"material_{index}", "role": "reference", "name": path.name}
                for index, path in enumerate(task_inputs(site_brief)[1:], start=1)
            ]],
            "subscribers": [],
            "deployment": {"required_releases": OPTIMIZATION_CYCLES + 1, "topic": "deployment.ready"},
            "run_policy": {"self_review": True},
            "evolution": {"require_verified_improvement": True,
                          **load_experiment(site_brief).get("evolution", {})},
        })


def config_options(args):
    options = list(args.cfg_options)
    if args.model:
        options[:0] = [f"model_name={args.model}", f"website_builder_agent.model_name={args.model}"]
    return options


def launch(args):
    config_path, site_brief = resolve_inputs(args)
    options = config_options(args)
    forwarded = ["run_meta_agent.py", "--config", str(config_path),
        "--agent-name", "website_builder_agent", "--task", build_task_text(site_brief),
        "--attach", *map(str, task_inputs(site_brief)), "--plan-mode", args.plan_mode,
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
