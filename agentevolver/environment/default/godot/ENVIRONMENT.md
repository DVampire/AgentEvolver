---
name: godot_environment
description: Shared Docker workspace, Godot CLI checks and native MCP gameplay observation/input.
version: 2.0.0
type: worker
---

# Godot project environment

The default Docker backend shares the canonical workspace path between base-container
Bash and a Godot/MCP container. The base also mounts session plan, log and extension
directories, so plan.md and artifact links retain their runtime paths. File authoring
uses existing workspace tools; there is no duplicate Godot file API. GameBuilder mounts
only this environment. It prepares the base container before its first turn.

Build `docker/godot` as `agentevolver/godot:4.7-b5fa8cb` before use; runtime never pulls
or builds images. The default host-run base is `python:3.12-slim` (Bash/Python authoring);
override base_image for additional development dependencies. A Model X launch reuses
its existing base and translates Docker mount sources via the host/container root mapping.
New containers run as the current UID/GID, with no network ports or Docker socket exposed.
Initialization/get_state starts no process. prepare_workspace starts only the base;
doctor or engine actions start the MCP container. Engine CLI remains available through
backend=local and binary_path/GODOT_BIN, but local mode does not provide native play.

## Setup and operations

1. `prepare_workspace`, then `doctor`: prepare file authoring, start the configured
   engine backend and record the actual Godot version. This does not check templates.
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

Commands use argv, session/project locks, deadlines and archived diagnostics under
`<workspace>/.godot-agent/`. Stop the native game before CLI imports/checks/exports.
Game saves use `.godot-agent/userdata` and survive container recreation. The image bundles
matching Linux debug/release export templates for its CPU architecture. Other export
platforms require their own matching templates and separate verification.
Honor user execution constraints. Base Bash uses foreground commands; Godot owns game
processes. Engine transport failures remove the Godot container while preserving base
file authoring. Session close removes both owned containers and the Bash route.

## Actual play

Use start_game to launch the selected project and wait for an authenticated bridge.
observe captures a rendered PNG; press_key holds/releases a key for at most two seconds;
move_mouse and click send ordinary player input. Actions capture the resulting frame,
which GameBuilder attaches to its model input. get_state returns the most recent captured
frame without recapturing; call observe for a fresh view of ongoing animation or motion.
inspect_runtime reads tree/ui/logs/errors; stop_game stops the game and removes its
transient bridge. No privileged hidden-state mutation tools are exposed.

Native desktop play is primary; Web export is optional. The image uses Xvfb/Mesa software
rendering, not a physical GPU benchmark. Input receipts, observed game changes and rendered
frames establish the test path; they do not establish campaign completeness or enjoyment.
Tests live in tests/test_godot_environment.py and use a small 3D fixture, not the campaign.

Official references:
- https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html
- https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_web.html
