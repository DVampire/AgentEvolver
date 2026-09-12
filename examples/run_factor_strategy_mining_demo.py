"""Launch the solo factor/strategy research demo through the shared agent runtime."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK_DIR = ROOT / "examples/tasks/factor_strategy_mining/signal_foundry"
DEFAULT_CONFIG = ROOT / "configs/factor_strategy_mining_demo.py"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model", help="Override the researcher model; image input is required.")
    parser.add_argument("--monitor-port", type=int, default=8766)
    parser.add_argument("--cfg-options", nargs="+", default=[], metavar="KEY=VALUE")
    return parser.parse_args(argv)


def task_inputs(task_dir):
    folder = Path(task_dir).expanduser().resolve()
    paths = [folder / "task.html", folder / "study.json"]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Task input not found: {path}")
    return paths


def launch(args):
    config_path = args.config.expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    options = list(args.cfg_options)
    if args.model:
        options[:0] = [f"model_name={args.model}", f"factor_strategy_mining_agent.model_name={args.model}"]
    brief, study = task_inputs(args.task_dir)
    forwarded = ["run_meta_agent.py", "--config", str(config_path),
        "--agent-name", "factor_strategy_mining_agent", "--task-file", str(brief),
        "--attach", str(study), "--plan-mode", "auto",
        "--monitor-port", str(args.monitor_port)]
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
