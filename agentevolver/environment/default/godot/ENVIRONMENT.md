---
name: godot_environment
description: Shared Docker workspace, Godot CLI checks and native MCP gameplay observation/input.
version: 2.1.0
type: worker
---

# Godot project environment

The default Docker backend shares the canonical workspace path between base-container
Bash and a Godot/MCP container. The base also mounts session plan, log and extension
directories, so plan.md and artifact links retain their runtime paths. File authoring
uses existing workspace tools; there is no duplicate Godot file API. GameBuilder mounts
only this environment. It prepares the base container before its first turn.

Build `docker/godot` as `agentevolver/godot:4.7-b5fa8cb-input2` before use; runtime never pulls
or builds images. The default host-run base is `python:3.12-slim` (Bash/Python authoring);
override base_image for additional development dependencies. A Model X launch reuses
its existing base and translates Docker mount sources via the host/container root mapping.
The owned base uses `base_network=bridge` for dependency/font/asset downloads;
set it to `none` for offline authoring. The default base has Bash and Python, not
curl or git: use Python urllib for HTTPS downloads or select a richer base image.
Godot itself always uses `--network none`. Model X retains its existing network policy.
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

`check_script` and `run_headless` resolve script/scene paths inside the project
selected by `open_project`, not the shared workspace root. Put probes under
`<project>/tests/` and use `tests/probe.gd` or `res://tests/probe.gd`. Absolute
paths must also stay inside that project; workspace `reports/` is for output.

Use `start_game` to launch the selected project (optionally a project-relative scene)
and wait for an authenticated bridge. `observe` captures a rendered PNG. Successful
input actions capture the resulting frame, which GameBuilder attaches to its model
input. `get_state` returns the most recent captured frame without recapturing;
call `observe` for a fresh view of ongoing animation or motion.

| Action | Player operation |
| --- | --- |
| `press_key` | Hold/release one key for 1..2000 ms |
| `press_keys` | Hold up to eight keys together for 1..10000 ms, such as movement plus sprint |
| `click`, `move_mouse` | Native viewport clicks and absolute/relative pointer or camera motion |
| `type_text` | Send 1..256 Unicode codepoints to a focused control; click to focus first |
| `input_sequence` | Ordered keyboard, mouse, gamepad, touch and named InputMap inputs; see below |
| `release_inputs` | Neutralize all controls held by this environment |
| `input_state` | Read actual keyboard/action/mouse state and pointer mode |
| `inspect_runtime` | Read scene tree, UI, logs or errors for diagnostics |

`input_sequence` accepts 1..32 objects with `type` and `arguments`. Supported types:
`key_down`, `key_up`, `key_tap`, `text`, `click`, `double_click`, `mouse_down`,
`mouse_up`, `mouse_move`, `drag`, `scroll`, `gamepad`, `touch`, `action_strength`,
`mouse_mode`, `wait`, `wait_frames`, `release_all`. Inspect the mounted action schema
for argument names; GUI shortcuts use `key_tap` modifier flags (`ctrl`, `shift`,
`alt`, `meta`). Held modifier keys also apply to mouse events. Gamepad axes/buttons
and multiple touch indices are supported. `action_strength` targets an existing
InputMap action; it cannot create bindings or mutate game state.
All parameters belong inside `arguments`: use
`{"type":"key_tap","arguments":{"key":"Enter"}}`, never
`{"type":"key_tap","key":"Enter"}`. The provider schema declares this nested
shape and the argument vocabulary; the connected MCP schema checks requirements
for the selected operation before input is sent.

Example arguments for moving forward while sprinting and turning the camera:

```json
{
  "steps": [
    {"type": "key_down", "arguments": {"key": "W"}},
    {"type": "key_down", "arguments": {"key": "Shift"}},
    {"type": "mouse_move", "arguments": {"x": 480, "y": 320, "relative_x": 40, "relative_y": 0}},
    {"type": "wait", "arguments": {"duration_ms": 800}}
  ],
  "release_at_end": true
}
```

Use the game's actual bindings. By default a sequence releases all held controls.
Set `release_at_end=false` to preserve holds across calls, then explicitly call
`release_inputs`. An idle lease releases them after `input_idle_seconds` (default
10 seconds, configurable from 0.25 to 60). Valid input sequences renew the lease;
observations do not. Each sequence has a 15-second deadline and at most 10000 ms
of explicit waits. All steps are schema-validated before the first input. Runtime
rejection releases held controls; transport failure/cancellation closes the engine.

`stop_game` stops the game and removes its transient bridge. If the game quits
through its own UI, subsequent observation recognizes the exit and permits restart.
No privileged hidden-state mutation tools are exposed. The pinned upstream image
has a checked local extension for mouse holds, double-clicks and mouse modifiers,
plus CJK fonts; the older image does not implement the full mouse contract.

This covers interaction inside the Godot game window through screenshots and input
events. It does not provide audio listening, host OS dialogs, a streamed human desktop
or physical device/force-feedback testing.

Native desktop play is primary; Web export is optional. The image uses Xvfb/Mesa software
rendering, not a physical GPU benchmark. Input receipts, observed game changes and rendered
frames establish the test path; they do not establish campaign completeness or enjoyment.
Tests live in tests/test_godot_environment.py and use a small 3D fixture, not the campaign.

Official references:
- https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html
- https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_web.html
