"""Launch one GameBuilder against an original Godot game task."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK_DIR = ROOT / "examples" / "tasks" / "game_development" / "tidebound_echoes"
DEFAULT_CONFIG = ROOT / "configs" / "game_development_demo.py"


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--task-dir", default=str(DEFAULT_TASK_DIR), help="Folder containing task.html.")
    parser.add_argument("--task-file", help="Override <task-dir>/task.html.")
    parser.add_argument("--milestone", choices=["vertical_slice", "campaign"], default="vertical_slice",
                        help="Default: a complete first playable slice plus the full campaign plan.")
    parser.add_argument("--evolution", choices=["required", "opportunistic"], default="required",
                        help="Required mode audits a verified capability improvement at completion.")
    parser.add_argument("--godot-bin", help="Select the local CLI-only backend with this Godot executable (no native play).")
    parser.add_argument("--model", help="GameBuilder model; must support screenshot input.")
    parser.add_argument("--plan-mode", choices=["off", "auto", "plan"], default="auto")
    parser.add_argument("--no-monitor", action="store_true")
    parser.add_argument("--monitor-port", type=int, default=8766)
    parser.add_argument("--cfg-options", nargs="+", default=[], metavar="KEY=VALUE")
    parser.add_argument("--print-task", action="store_true", help="Print task text without starting the agent or engine.")
    return parser.parse_args(argv)


def _existing_file(raw, role):
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{role} file not found: {path}")
    return path


def build_task_text(brief: Path, milestone: str, require_evolution: bool) -> str:
    from agentevolver.task.context import load_task_document, render_manifest

    scope = {
        "vertical_slice": (
            "Deliver milestone M1 in the task: the complete prologue and chapter-one playable slice, "
            "alongside the entire long-campaign design/content ledger. Later chapters remain "
            "planned, not delivered. Do not claim the full campaign is playable."
        ),
        "campaign": (
            "Deliver milestone M3 in the task: the complete authored playable campaign and "
            "requested postgame. A vertical slice is only an intermediate milestone. If the "
            "run cannot finish, report partial progress and a resume point, not completion."
        ),
    }[milestone]
    experiment = (
        "This run requires a verified reusable agent-system capability improvement in addition "
        "to the game milestone. Invoke self_evolving_skill after a real self-play observation. "
        "Inspect and probe the existing method; preserve pre-registration observation and "
        "baseline call IDs. Diagnose capability_gap with user_need, required_operation, "
        "limitation and acceptance_criterion. Register only a justified candidate; evaluate "
        "the exact returned version against the baseline (kind=comparison) and an independent "
        "case (kind=reuse or regression), using actual evidence and measured cost. Record keep "
        "only on pass, then use it synchronously in subsequent real development and call "
        "adoption_tool record_use with module, name, version, consumer_call_id, evidence_ids "
        "and outcome. Loading a skill alone is not using its method. Failed candidates must "
        "be rolled back/unloaded. Runtime audits these receipts, including text-only endings. "
        "A missing genuine gap or missing verification leaves the evolution outcome unmet; "
        "never fabricate failure, evidence or player feedback to satisfy it."
        if require_evolution else
        "Follow shared self-evolution rules when an evidenced reusable limitation arises. "
        "No verified-improvement completion quota is imposed in opportunistic mode."
    )
    instructions = (
        "You are the sole GameBuilder. No user/persona/reviewer agents or subscriptions. "
        "Design, implement, visually play the native Godot game, critique, improve, "
        "and track campaign progress yourself.\n\n"
        + scope + "\n\n" + experiment + "\n\n"
        "Use Bash for project files and bounded foreground commands; godot_environment is "
        "the only mounted environment and owns engine operations. The base and Godot Docker "
        "containers share canonical workspace paths. Use doctor/open_project/import_project, "
        "then start_game/observe/press_keys/move_mouse/click/type_text/input_sequence for native play. "
        "Use input_sequence for combined controls, shortcuts, drag/scroll, gamepad and touch. "
        "Release retained controls explicitly with release_inputs within the idle lease; "
        "use input_state and inspect_runtime for diagnostics and verify visible outcomes. "
        "Stop the game before CLI checks, imports or exports. Keep failed rendering/input "
        "acceptance blocked; never treat headless checks as play. Use Godot 4 and GDScript, with "
        "native delivery as the primary target and Web export optional. A website mockup is not a game. "
        "Keep product milestone evidence and agent evolution evidence separate. "
        "Honor explicit user execution/testing constraints; unrun checks remain pending."
    )
    return render_manifest(load_task_document(str(brief)).content, instructions, {
        "attachments": [{"id": "game_brief", "role": "requirements"}],
        "subscribers": [],
        "game_development": {"engine": "godot", "milestone": milestone, "self_play": True},
        "evolution": {"require_verified_improvement": require_evolution},
    })


def launch(args):
    config_path = _existing_file(args.config, "config")
    brief = _existing_file(args.task_file or str(Path(args.task_dir) / "task.html"), "game task")
    task = build_task_text(brief, args.milestone, args.evolution == "required")
    if args.print_task:
        print(task)
        return
    options = list(args.cfg_options)
    if args.model:
        options[:0] = [f"model_name={args.model}", f"game_builder_agent.model_name={args.model}"]
    if args.godot_bin:
        options.insert(0, "godot_environment.backend=local")
        options.insert(0, f"godot_environment.binary_path={Path(args.godot_bin).expanduser().resolve()}")
    forwarded = ["run_meta_agent.py", "--config", str(config_path),
                 "--agent-name", "game_builder_agent", "--task", task, "--attach", str(brief),
                 "--plan-mode", args.plan_mode, "--monitor-port", str(args.monitor_port)]
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
