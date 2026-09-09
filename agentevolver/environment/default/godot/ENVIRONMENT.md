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
The external Bash container backend is rejected to avoid editing one filesystem while
running the engine on another. ComputerEnvironment's separate desktop is not connected.

## Setup and operations

1. `doctor`: resolve `binary_path`, then `GODOT_BIN`, otherwise `godot`/`godot4` on
   PATH. Record an exact Godot 4 editor version. This does not check templates.
2. Author source using apply_patch and choose `open_project(project_path=...)`.
   The directory must already contain project.godot inside the task workspace.
3. `import_project`: headless editor import. Read errors even when exit is zero.
4. `check_script(script=...)`: parse one GDScript file with --check-only. Import
   dependencies first. This is not an all-project verifier.
5. `run_headless`: main scene, optional .tscn/.scn, or SceneTree/MainLoop .gd script.
   Engine iteration and wall-time limits both apply. Assertion scripts must explicitly
   report completion and fail with a nonzero exit. Automatic frame exit is not a pass.
6. `export_project`: use the exact preset name from export_presets.cfg and matching
   installed export templates. Use fresh output such as
   `builds/r001/web/index.html` outside `game/`. No old output directory is reused.
   A nonempty artifact and clean command are necessary, not sufficient, for a good build.
7. `logs`: last operation, bounded output, exit, timeout, error flag and full log path.

Commands use argv rather than shell evaluation, serialize session/project access,
archive stdout/stderr under `<workspace>/.godot-agent/`, and reap owned processes on
completion, timeout or cancellation. This adapter does not enforce OS isolation on
project scripts/plugins. Honor the session's permission and execution constraints.

## Actual play

Export a GDScript Godot 4 project with the Compatibility renderer and a single-threaded
Web preset for browser preview. Keep index.html and its generated companion files
together; use deploy_tool on the export directory. Play its exact preview URL through
browser_environment. Browser screenshots are delivered by GameBuilderAgent.

Godot canvas controls are not DOM elements. Use visible canvas coordinates, focus and
ordinary input; browser command can issue bounded held keys on the existing page.
After input, observe the resulting frame. Scripts/telemetry that mutate hidden game
state are useful diagnostics, not player-experience evidence. Native desktop play is
a separate future adapter; do not claim this headless environment supplies it.

Official references:
- https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html
- https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_web.html
