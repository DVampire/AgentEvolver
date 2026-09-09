---
name: godot_environment
description: Local Godot 4 project lifecycle with bounded CLI operations and archived diagnostics.
version: 1.0.0
type: worker
---

# Godot project environment

This environment shares the local task workspace with Bash and apply_patch. It owns
the selected project and command evidence per session. Initialization/get_state do
not start Godot. Engine commands execute only on explicit actions; nothing is installed.
It is not a sandbox, remote editor, game input API or visual gameplay observer.
The external Bash container backend is currently rejected because shared mounts and
the Godot container adapter are not wired. The target architecture uses base-container
Bash for file operations and a Godot container for engine operations on the same workspace.
The demo mounts only this environment; background-job and browser environments are absent.

## Setup and operations

1. `doctor`: resolve `binary_path`, then `GODOT_BIN`, otherwise `godot`/`godot4` on
   PATH. Record an exact Godot 4 editor version. This does not check templates.
2. Author source using workspace tools and choose `open_project(project_path=...)`.
   The directory must already contain project.godot inside the task workspace.
3. `import_project`: headless editor import. Read errors even when exit is zero.
4. `check_script(script=...)`: parse one GDScript file with --check-only. Import
   dependencies first. This is not an all-project verifier.
5. `run_headless`: main scene, optional .tscn/.scn, or SceneTree/MainLoop .gd script.
   Engine iteration and wall-time limits both apply. Assertion scripts must explicitly
   report completion and fail with a nonzero exit. Automatic frame exit is not a pass.
6. `export_project`: use the exact preset name from export_presets.cfg and matching
   installed export templates. Use fresh output such as
   `builds/r001/desktop/game.x86_64` outside `game/`. No old output directory is reused.
   A nonempty artifact and clean command are necessary, not sufficient, for a good build.
7. `logs`: last operation, bounded output, exit, timeout, error flag and full log path.

Commands use argv rather than shell evaluation, serialize session/project access,
archive stdout/stderr under `<workspace>/.godot-agent/`, and reap owned processes on
completion, timeout or cancellation. This adapter does not enforce OS isolation on
project scripts/plugins. Honor the session's permission and execution constraints.

## Actual play

Native desktop play is the primary target, with Web export optional. The native MCP
runtime bridge, rendered screenshots, player input and model image delivery are not
implemented yet. Do not invent actions or report a headless run as visual self-play.
Keep play-dependent acceptance blocked until the native integration is available.

The future native loop must observe frames before and after ordinary player inputs,
bound held keys and guarantee release. Scripts/telemetry that mutate hidden game
state remain diagnostics, not player-experience evidence. File edits require an
appropriate import/reload or restart before observations certify the new revision.

Official references:
- https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html
- https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_web.html
