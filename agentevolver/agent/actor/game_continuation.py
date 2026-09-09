"""Carry authored game files into a new run without replaying old agent actions."""
import json
import shutil
from pathlib import Path


def seed_game_session(source_session: str, workspace: Path, plan: Path) -> dict:
    source = Path(source_session).expanduser().resolve()
    workspace, plan = workspace.resolve(), plan.resolve()
    if not (source / "session.json").is_file() or not (source / "workspace").is_dir():
        raise ValueError("continue_from must name an existing session with session.json and workspace/")
    from agentevolver.visual.run.server import process_start, read_json

    monitor = read_json(source / "log/run_monitor.json", {})
    if monitor.get("launcher_start") and process_start(monitor.get("launcher_pid")) == monitor["launcher_start"]:
        raise ValueError("Stop the source session before copying its live game files")
    if workspace.is_relative_to(source) or source.is_relative_to(workspace):
        raise ValueError("Continuation requires a separate destination session")
    if any(workspace.iterdir()) or (plan.exists() and any(plan.iterdir())):
        raise ValueError("Continuation destination workspace and plan must be empty")
    if (source / "plan/plan.md").is_symlink() or (source / "workspace/continuation.json").is_symlink():
        raise ValueError("Continuation metadata and plan must be ordinary files")
    # Copy links as links, never follow them into unrelated host files. Imported
    # Godot caches are regenerated; authored files, saves and old screenshots stay.
    shutil.copytree(source / "workspace", workspace, dirs_exist_ok=True,
                    symlinks=True, ignore=shutil.ignore_patterns(".godot"))
    if (source / "plan").is_dir():
        shutil.copytree(source / "plan", plan, dirs_exist_ok=True, symlinks=True)
    plan_file = plan / "plan.md"
    if plan_file.is_symlink():
        raise ValueError("Continuation plan.md must be an ordinary file")
    if plan_file.is_file():
        text = plan_file.read_text()
        text = text.replace(str(source / "workspace"), str(workspace)).replace(str(source / "plan"), str(plan))
        plan_file.write_text(text)
    receipt = {
        "source_session": str(source), "mode": "authored_files_and_plan",
        "plan": str(plan_file), "requires_plan_reconciliation": True,
        "note": "Fresh model conversation, budget and evolution audit. Copied artifacts are historical, not verification of this run.",
    }
    (workspace / "continuation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
