"""Session-scoped Godot CLI and native MCP gameplay on a shared Docker workspace.

Construction/state observation starts no processes. Workspace preparation starts only
the base authoring container; doctor or engine actions start the Godot MCP container.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import uuid
from pathlib import Path
from typing import Any, Dict

from pydantic import Field, PrivateAttr

from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment, ScreenshotInfo
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import ENVIRONMENT
from agentevolver.session import isolated_workspace_root, resolve_workspace_root
from .inputs import InputSteps


@ENVIRONMENT.register_module(force=True)
class GodotEnvironment(Environment):
    name: str = Field(default="godot_environment")
    description: str = Field(default=(
        "Godot 4 project workspace: engine discovery, imports, GDScript parsing, "
        "bounded CLI checks, exports, native Docker/MCP launch, screenshots and player input."
    ))
    metadata: Dict[str, Any] = Field(default={"has_vision": True, "engine": "godot"})
    backend: str = Field(default="docker", pattern="^(docker|local)$")
    image: str = Field(default="agentevolver/godot:4.7-b5fa8cb-input2")
    base_image: str = Field(default="python:3.12-slim")
    base_network: str = Field(default="bridge", pattern="^(bridge|none)$")
    enable_evolving: bool = Field(default=False)
    binary_path: str = Field(default="")
    max_command_seconds: int = Field(default=300, ge=10, le=600)
    _sessions: dict = PrivateAttr(default_factory=dict)
    _locks: dict = PrivateAttr(default_factory=dict)
    _project_locks: dict = PrivateAttr(default_factory=dict)
    _processes: dict = PrivateAttr(default_factory=dict)
    _runtimes: dict = PrivateAttr(default_factory=dict)
    input_idle_seconds: float = Field(default=10.0, ge=0.25, le=60)
    _input_watchdogs: dict = PrivateAttr(default_factory=dict)

    @staticmethod
    def _sid(ctx):
        return str(getattr(ctx, "id", "") or "default")

    def _session(self, ctx):
        sid = self._sid(ctx)
        root = resolve_workspace_root(ctx)
        if not root:
            raise ValueError("No bound workspace. Start through the task/session launcher.")
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        rec = self._sessions.setdefault(sid, {"workspace": root, "project": None, "last": {}})
        if rec["workspace"] != root:
            raise ValueError("The session workspace changed; close this environment session first.")
        return sid, rec

    @staticmethod
    def _inside(raw, root):
        path = Path(raw).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Path must stay inside {root}: {path}")
        return path

    def _binary(self):
        if self.backend == "docker":
            return "/usr/local/bin/godot"
        if os.environ.get("AGENTEVOLVER_EXEC_CONTAINER", "").strip():
            raise ValueError(
                "The local Godot backend cannot share an externally selected Bash container. "
                "Use backend=docker with its owned base container, or run locally without that override."
            )
        configured = self.binary_path or os.environ.get("GODOT_BIN", "").strip()
        if configured:
            found = shutil.which(os.path.expanduser(configured))
        else:
            found = shutil.which("godot") or shutil.which("godot4")
        if not found:
            raise ValueError(
                "Godot editor binary not found. Configure godot_environment.binary_path or "
                "GODOT_BIN with a Godot 4 editor executable (not an export template). "
                "Install matching export templates separately before exporting."
            )
        return str(Path(found).resolve())

    def _runtime(self, sid, rec):
        from agentevolver.paths import path_manager

        from .runtime import DockerRuntime

        if sid not in self._runtimes:
            roots = path_manager.session_roots()
            mounts = [roots[key] for key in ("plan", "log", "extension") if key in roots]
            references = [roots[key] for key in ("package", "shared_extension")
                          if key in roots and roots[key].is_dir()]
            self._runtimes[sid] = DockerRuntime(
                rec["workspace"], self.image, self.base_image, mounts,
                read_only_mounts=references, base_network=self.base_network)
        return self._runtimes[sid]

    @environment_manager.action(
        name="prepare_workspace", read_only=False, destructive=False,
        description="Prepare the shared base Docker workspace for Bash file operations. No game is launched; images must already be installed.",
    )
    async def prepare_workspace(self, ctx=None, **kwargs):
        sid = self._sid(ctx)
        async with self._locks.setdefault(sid, asyncio.Lock()):
            try:
                sid, rec = self._session(ctx)
                if self.backend == "docker":
                    permission = permission_manager.check_declared(
                        self.name, PermissionRequest(op=Operation.BASH, target="docker run game workspace"),
                        mode=self.permission_mode, workspace=isolated_workspace_root(ctx),
                    )
                    if not permission.allowed:
                        return {"success": False, "message": permission.reason}
                    await self._runtime(sid, rec).prepare_base()
                return {"success": True, "message": f"Workspace ready: {rec['workspace']}",
                        "extra": {"workspace": str(rec["workspace"]), "backend": self.backend}}
            except (OSError, ValueError, RuntimeError) as error:
                return {"success": False, "message": str(error)}

    @staticmethod
    async def _stop(proc):
        # Each command owns a process group. Reap children even when its parent
        # exited normally, so an imported editor plugin cannot leave a worker behind.
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            elif proc.returncode is None:
                proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            if os.name != "posix":
                proc.kill()
        finally:
            # The parent can exit before a child that ignored SIGTERM. Its wait()
            # alone does not prove that the owned process group has disappeared.
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await proc.wait()

    @staticmethod
    def _read_log(path):
        error = False
        # Stream the archive for diagnostics; keep only a bounded tail in context.
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if re.search(r"(?:^|\s)(?:SCRIPT ERROR|ERROR|Parse Error):", line):
                    error = True
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 12000))
            tail = stream.read().decode("utf-8", errors="replace")
        return tail, error

    async def _execute(self, sid, rec, arguments, timeout, ctx, operation, output_path=None):
        argv = [self._binary(), *arguments]
        permission = permission_manager.check_declared(
            self.name, PermissionRequest(op=Operation.BASH, target=shlex.join(argv)),
            mode=self.permission_mode, workspace=isolated_workspace_root(ctx),
        )
        if not permission.allowed:
            message = f"Permission denied: {permission.reason}"
            extra = {"operation": operation, "command": argv, "executed": False}
            rec["last"] = {**extra, "success": False, "message": message}
            return {"success": False, "message": message, "extra": extra}
        if rec.get("running"):
            raise ValueError("Stop the running game before CLI imports, checks or exports")
        if self.backend == "docker":
            runtime = self._runtime(sid, rec)
            await runtime.start()
            argv = [os.environ.get("AGENTEVOLVER_DOCKER", "docker"), "exec", runtime.name,
                    "timeout", "--kill-after=3s", str(timeout), *argv]
        log_dir = self._inside(
            Path(".godot-agent") / hashlib.sha256(sid.encode()).hexdigest()[:16], rec["workspace"],
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{operation}-{uuid.uuid4().hex}.log"
        timed_out = False
        cancelled = False
        rec["last"] = {"operation": operation, "command": argv, "success": False,
                       "status": "running", "log_path": str(log_path)}
        with log_path.open("wb") as output:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(rec["project"] or rec["workspace"]),
                stdout=output, stderr=asyncio.subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
            self._processes[sid] = proc
            try:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=timeout + (5 if self.backend == "docker" else 0))
                    if self.backend == "docker" and proc.returncode in (124, 137):
                        timed_out = True
                except asyncio.TimeoutError:
                    timed_out = True
                except asyncio.CancelledError:
                    cancelled = True
                    raise
            finally:
                await asyncio.shield(self._stop(proc))
                if self.backend == "docker" and (timed_out or cancelled):
                    await asyncio.shield(self._runtime(sid, rec).close(remove_base=False))
                self._processes.pop(sid, None)
                if cancelled:
                    rec["last"].update(status="cancelled", cancelled=True, exit_code=proc.returncode)
        tail, errors = self._read_log(log_path)
        ok = proc.returncode == 0 and not timed_out and not errors
        extra = dict(operation=operation, command=argv, project=str(rec["project"] or ""),
                     log_path=str(log_path), exit_code=proc.returncode, timed_out=timed_out,
                     engine_errors=errors, output=tail, executed=True, status="finished")
        rec["last"] = {**extra, "success": ok}
        return {"success": ok, "message": (
            f"Godot {operation}: exit={proc.returncode}, timeout={timed_out}, "
            f"engine_errors={errors}. Log: {log_path}\n{tail}"
        ), "extra": extra}

    async def _perform(self, operation, ctx, timeout, **options):
        sid = self._sid(ctx)
        async with self._locks.setdefault(sid, asyncio.Lock()):
            try:
                sid, rec = self._session(ctx)
                if not 1 <= timeout <= self.max_command_seconds:
                    raise ValueError(f"timeout must be 1..{self.max_command_seconds} seconds")
                if operation == "doctor":
                    result = await self._execute(sid, rec, ["--headless", "--version"], timeout, ctx, operation)
                    version = result.get("extra", {}).get("output", "").strip()
                    if result["success"] and not re.match(r"4\.\d+", version):
                        result.update(success=False, message=f"Expected Godot 4; binary reported: {version}")
                        rec["last"]["success"] = False
                    result["message"] += "\nVersion discovery does not verify export templates, rendering or gameplay."
                    return result
                project = rec["project"]
                if project is None or not (project / "project.godot").is_file():
                    raise ValueError("Use open_project on an existing project.godot directory first.")
                project = self._inside(project, rec["workspace"])
                async with self._project_locks.setdefault(str(project), asyncio.Lock()):
                    argv = ["--headless", "--path", str(project)]
                    output = None
                    if operation == "import":
                        argv += ["--import"]
                    elif operation in ("check_script", "run_headless"):
                        script = options.get("script", "")
                        scene = options.get("scene", "")
                        if script and scene:
                            raise ValueError("Choose either script or scene, not both.")
                        if operation == "check_script" and not script:
                            raise ValueError("check_script requires a .gd script path")
                        if script or scene:
                            # These are project resources, unlike workspace reports
                            # or exports. Explain the distinction before engine launch.
                            candidate = Path((script or scene).removeprefix("res://"))
                            candidate = (candidate if candidate.is_absolute() else project / candidate).resolve()
                            if not candidate.is_relative_to(project):
                                raise ValueError(
                                    f"Script/scene must be inside the selected Godot project {project}. "
                                    "Put test scripts under that project's tests/ directory and pass "
                                    "tests/example.gd or res://tests/example.gd. "
                                    f"Workspace reports are not project resources: {candidate}"
                                )
                            resource = self._inside((script or scene).removeprefix("res://"), project)
                            suffixes = (".gd",) if script else (".tscn", ".scn")
                            if not resource.is_file() or resource.suffix not in suffixes:
                                raise ValueError(f"Expected an existing {suffixes} resource: {resource}")
                            argv += ["--script", str(resource)] if script else [str(resource)]
                        if operation == "check_script":
                            argv += ["--check-only"]
                        else:
                            frames = options["frames"]
                            if not 1 <= frames <= 36000:
                                raise ValueError("frames must be 1..36000 (engine iterations, not wall time)")
                            argv += ["--quit-after", str(frames)]
                    elif operation == "export":
                        preset = options["preset"].strip()
                        if not preset or preset.startswith("-"):
                            raise ValueError("Use an exact nonempty export preset name.")
                        if not (project / "export_presets.cfg").is_file():
                            raise ValueError("Missing export_presets.cfg; create a preset for the installed engine.")
                        output = self._inside(options["output_path"], rec["workspace"])
                        if output.is_relative_to(project):
                            raise ValueError("Export to a fresh directory outside the source project, inside the workspace.")
                        if output.exists() or (output.parent.exists() and any(output.parent.iterdir())):
                            raise ValueError("Export directory must be empty; use a fresh build revision directory.")
                        argv += ["--export-debug" if options["debug"] else "--export-release", preset, str(output)]
                    else:
                        raise ValueError(f"Unknown operation: {operation}")
                    result = await self._execute(sid, rec, argv, timeout, ctx, operation, output_path=output)
                    if output is not None:
                        exists = output.is_file() and output.stat().st_size > 0
                        result["extra"].update(output_path=str(output), artifact_exists=exists)
                        result["success"] = result["success"] and exists
                        result["message"] += f"\nExport artifact: {output}; nonempty={exists}. Play it to verify behavior."
                        rec["last"].update(result["extra"], success=result["success"])
                    if operation == "run_headless":
                        result["message"] += "\nBounded headless execution only; no visual, audio or fun verdict."
                    return result
            except (OSError, ValueError, RuntimeError) as error:
                failure = {"success": False, "message": str(error)}
                if sid in self._sessions:
                    self._sessions[sid]["last"] = {"operation": operation, **failure}
                return failure

    @environment_manager.action(
        name="doctor", read_only=False, destructive=False,
        description="Prepare the configured backend and report its exact Godot version. Does not install images or validate export templates.",
    )
    async def doctor(self, ctx=None, **kwargs):
        return await self._perform("doctor", ctx, 10)

    @environment_manager.action(
        name="open_project", read_only=True, destructive=False,
        description="Select an existing Godot project directory inside this task workspace. Relative paths resolve from the workspace. Does not launch the engine.",
    )
    async def open_project(self, project_path: str, ctx=None, **kwargs):
        sid = self._sid(ctx)
        async with self._locks.setdefault(sid, asyncio.Lock()):
            try:
                sid, rec = self._session(ctx)
                if rec.get("running"):
                    raise ValueError("Stop the current game before selecting another project")
                project = self._inside(project_path, rec["workspace"])
                if not (project / "project.godot").is_file():
                    raise ValueError(f"Missing project.godot in {project}. Author the project first.")
                rec.update(project=project, last={})
                return {"success": True, "message": f"Selected Godot project: {project}",
                        "extra": {"project": str(project)}}
            except (OSError, ValueError) as error:
                return {"success": False, "message": str(error)}

    @environment_manager.action(
        name="import_project", read_only=False, destructive=False,
        description="Import the selected project's assets using Godot --import. Capture diagnostics and exit status; import success is not gameplay verification.",
    )
    async def import_project(self, timeout: int = 180, ctx=None, **kwargs):
        return await self._perform("import", ctx, timeout)

    @environment_manager.action(
        name="check_script", read_only=False, destructive=False,
        description="Parse ONE .gd file with --script --check-only. The file must be inside the selected Godot project, including when using an absolute path. Use project-relative or res:// paths. This does not validate all project scripts or gameplay.",
    )
    async def check_script(self, script: str, timeout: int = 60, ctx=None, **kwargs):
        """Parse a project resource.

        Args:
            script: Existing .gd file inside the selected project, e.g. tests/check.gd or res://tests/check.gd; workspace reports outside the project are not accepted.
            timeout: Wall-clock limit in seconds.
        """
        return await self._perform("check_script", ctx, timeout, script=script)

    @environment_manager.action(
        name="run_headless", read_only=False, destructive=False,
        description="Run the main scene, a scene, or a SceneTree/MainLoop script without a window. Scripts/scenes must be inside the selected Godot project; place probes in its tests/ directory, not workspace reports/. Bounded by engine iterations AND wall time. A clean exit is not an assertion pass or visual play evidence.",
    )
    async def run_headless(self, scene: str = "", script: str = "", frames: int = 120,
                           timeout: int = 60, ctx=None, **kwargs):
        """Run a bounded project scene or script.

        Args:
            scene: Project-relative, res:// or absolute .tscn/.scn path inside the selected project; omit for its main scene. Cannot combine with script.
            script: SceneTree/MainLoop .gd file inside the selected project, e.g. tests/baseline.gd. Absolute paths outside the project are rejected. Cannot combine with scene.
            frames: Engine iteration limit; does not assert test success.
            timeout: Wall-clock limit in seconds.
        """
        return await self._perform("run_headless", ctx, timeout, scene=scene, script=script, frames=frames)

    @environment_manager.action(
        name="export_project", read_only=False, destructive=False,
        description="Export an exact configured preset with matching installed templates. output_path is workspace-relative or absolute; its directory must be empty and outside the source project. Web output should end in index.html.",
    )
    async def export_project(self, preset: str, output_path: str, debug: bool = True,
                             timeout: int = 300, ctx=None, **kwargs):
        return await self._perform("export", ctx, timeout, preset=preset, output_path=output_path, debug=debug)

    @environment_manager.action(
        name="logs", read_only=True, destructive=False,
        description="Read the last Godot command's archived log tail, exit status and diagnostic flags for this session.",
    )
    async def logs(self, ctx=None, **kwargs):
        rec = self._sessions.get(self._sid(ctx), {})
        last = dict(rec.get("last", {}))
        if not last:
            return {"success": False, "message": "No Godot operation recorded for this session."}
        if last.get("log_path"):
            try:
                last["output"], last["engine_errors"] = self._read_log(Path(last["log_path"]))
            except OSError as error:
                return {"success": False, "message": f"Cannot read Godot log: {error}", "extra": last}
        return {"success": True, "message": str(last), "extra": last}

    async def _mcp(self, sid, rec, tool, arguments):
        from .runtime import mcp_succeeded

        result = await self._runtime(sid, rec).call(tool, arguments)
        messages = [part.text for part in result.content if part.type == "text"]
        ok = mcp_succeeded(result)
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured.get("success") is False:
            ok = False
        for message in messages:
            try:
                payload = json.loads(message)
                if isinstance(payload, dict) and payload.get("success") is False:
                    ok = False
            except ValueError:
                pass
        # Exact lifecycle errors from the pinned MCP backend: a player can quit
        # from inside the game, independently of stop_game.
        if not ok and any(message in (
            "No active Godot process. Use run_project first.",
            "No active Godot process to stop.",
        ) for message in messages):
            self._cancel_input_watchdog(sid)
            rec.update(running=False, screenshots=[], held_inputs={})
            if tool == "stop_project":
                ok = True
        shots = []
        for part in result.content:
            if part.type != "image":
                continue
            if part.mimeType != "image/png":
                raise ValueError(f"Unexpected screenshot MIME type: {part.mimeType}")
            raw = base64.b64decode(part.data, validate=True)
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid PNG returned by MCP")
            path = rec["workspace"] / ".godot-agent" / f"frame-{uuid.uuid4().hex}.png"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
            shots.append(ScreenshotInfo(screenshot=part.data, screenshot_path=str(path),
                                        screenshot_description=f"Godot {tool}: {path.name}"))
        if tool == "game_screenshot" and not shots:
            ok = False
            messages.append("MCP returned no rendered PNG")
        if shots:
            rec["screenshots"] = shots
        if not ok:
            rec["screenshots"] = []
            if any("Not connected to game interaction server" in message for message in messages):
                rec["bridge_error"] = "Game process may still be running, but its interaction bridge is disconnected. Stop/start explicitly to recover; inputs were not retried."
        else:
            rec.pop("bridge_error", None)
        last = {"operation": tool, "success": ok, "output": "\n".join(messages)[-12000:]}
        rec["last"] = last
        return {"success": ok, "message": last["output"],
                "extra": {"structured": structured, "screenshots": shots}}

    def _cancel_input_watchdog(self, sid):
        task = self._input_watchdogs.pop(sid, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _release_inputs(self, sid, rec):
        self._cancel_input_watchdog(sid)
        held = rec.setdefault("held_inputs", {})
        for token, (tool, args) in list(held.items()):
            result = await self._mcp(sid, rec, tool, args)
            if not result["success"]:
                raise RuntimeError(f"Could not release {token}: {result['message']}")
            held.pop(token, None)

    def _arm_input_watchdog(self, sid, rec):
        self._cancel_input_watchdog(sid)
        if not rec.get("held_inputs"):
            return

        async def expire():
            try:
                await asyncio.sleep(self.input_idle_seconds)
                async with self._locks[sid]:
                    if self._sessions.get(sid) is rec and rec.get("running"):
                        await self._release_inputs(sid, rec)
                        rec["screenshots"] = []
                        rec["last"] = {"operation": "input_idle_release", "success": True,
                                       "message": "Idle input lease expired; held controls were released. Call observe for a fresh frame."}
            except asyncio.CancelledError:
                pass
            except Exception as error:
                rec.update(running=False, screenshots=[], held_inputs={},
                           last={"operation": "input_idle_release", "success": False, "message": str(error)})
                runtime = self._runtimes.get(sid)
                if runtime:
                    await runtime.close(remove_base=False)

        self._input_watchdogs[sid] = asyncio.create_task(expire(), name=f"godot-input-lease-{sid}")

    async def _input_sequence(self, sid, rec, steps, release_at_end):
        from .inputs import compile_steps, remember_input

        # Validate the whole sequence before the first input; an invalid later
        # step must not leave an earlier key down or destroy a healthy game.
        try:
            compiled = compile_steps(steps, self._runtime(sid, rec).schemas)
        except (ValueError, TypeError) as error:
            return {"success": False, "message": str(error), "extra": {"executed_steps": 0}}
        rec["screenshots"] = []
        held = rec.setdefault("held_inputs", {})
        trace = []
        async with asyncio.timeout(15):
            for kind, tool, args in compiled:
                if kind == "wait":
                    await asyncio.sleep(args["duration_ms"] / 1000)
                    result = {"success": True, "message": "Wait completed"}
                elif kind == "release_all":
                    await self._release_inputs(sid, rec)
                    result = {"success": True, "message": "Held controls released"}
                else:
                    if kind == "double_click":
                        first = await self._mcp(sid, rec, "game_click", {k: v for k, v in args.items() if k != "doubleClick"})
                        if not first["success"]:
                            await self._release_inputs(sid, rec)
                            return first
                    result = await self._mcp(sid, rec, tool, args)
                    if result["success"]:
                        remember_input(held, kind, args)
                trace.append({"type": kind, "success": result["success"], "message": result["message"]})
                if not result["success"]:
                    await self._release_inputs(sid, rec)
                    return {"success": False, "message": result["message"], "extra": {"steps": trace}}
            if release_at_end:
                await self._release_inputs(sid, rec)
        self._arm_input_watchdog(sid, rec)
        return {"success": True, "message": f"Completed {len(trace)} input steps; held={list(held)}",
                "extra": {"steps": trace, "held_inputs": list(held)}}

    async def _native(self, operation, ctx, **options):
        sid = self._sid(ctx)
        async with self._locks.setdefault(sid, asyncio.Lock()):
            try:
                sid, rec = self._session(ctx)
                if self.backend != "docker":
                    raise ValueError("Native play requires the Docker backend")
                permission = permission_manager.check_declared(
                    self.name, PermissionRequest(op=Operation.BASH, target=f"Godot MCP {operation}"),
                    mode=self.permission_mode, workspace=isolated_workspace_root(ctx),
                )
                if not permission.allowed:
                    return {"success": False, "message": permission.reason}
                if operation == "start":
                    project = rec["project"]
                    if project is None or not (project / "project.godot").is_file():
                        raise ValueError("Select an existing project with open_project first")
                    self._inside(project, rec["workspace"])
                    if rec.get("running"):
                        raise ValueError("Game already running; stop before restarting")
                    args = {"projectPath": str(project), "timingMode": "realtime"}
                    scene = options.get("scene", "")
                    if scene:
                        path = self._inside(scene.removeprefix("res://"), project)
                        if not path.is_file() or path.suffix not in (".tscn", ".scn"):
                            raise ValueError("Scene must be an existing Godot scene inside the project")
                        args["scene"] = path.relative_to(project).as_posix()
                    result = await self._mcp(sid, rec, "run_project", args)
                    rec["running"] = result["success"]
                elif operation == "stop":
                    self._cancel_input_watchdog(sid)
                    if not rec.get("running"):
                        return {"success": True, "message": "No game running"}
                    result = await self._mcp(sid, rec, "stop_project", {})
                    if result["success"]:
                        rec.update(running=False, screenshots=[], held_inputs={})
                    return result
                else:
                    if not rec.get("running"):
                        raise ValueError("No running game; call start_game first")
                    if operation == "observe":
                        return await self._mcp(sid, rec, "game_screenshot", {})
                    if operation == "inspect":
                        mapping = {"tree": "game_get_scene_tree", "ui": "game_get_ui",
                                   "logs": "game_get_logs", "errors": "game_get_errors"}
                        if options["kind"] not in mapping:
                            raise ValueError("kind must be tree, ui, logs or errors")
                        return await self._mcp(sid, rec, mapping[options["kind"]], {})
                    if operation == "input_state":
                        return await self._mcp(sid, rec, "game_input_state", {"action": "query", **options})
                    if operation == "sequence":
                        result = await self._input_sequence(sid, rec, **options)
                    elif operation == "key":
                        key, duration = options["key"], options["duration_ms"]
                        if not key or not 1 <= duration <= 2000:
                            raise ValueError("A key and duration_ms in 1..2000 are required")
                        result = await self._input_sequence(sid, rec, [
                            {"type": "key_down", "arguments": {"key": key}},
                            {"type": "wait", "arguments": {"duration_ms": duration}},
                            {"type": "key_up", "arguments": {"key": key}},
                        ], release_at_end=False)
                    elif operation == "click":
                        result = await self._input_sequence(sid, rec, [
                            {"type": "click", "arguments": options}], release_at_end=False)
                    elif operation == "mouse":
                        rec["screenshots"] = []
                        result = await self._input_sequence(sid, rec, [
                            {"type": "mouse_move", "arguments": options}], release_at_end=False)
                    else:
                        raise ValueError(f"Unknown native operation: {operation}")
                if result["success"]:
                    frame = await self._mcp(sid, rec, "game_screenshot", {})
                    result["extra"]["screenshots"] = frame["extra"]["screenshots"]
                    result["extra"]["screenshot_success"] = frame["success"]
                    if not frame["success"]:
                        result.update(success=False, message=f"Operation completed but screenshot failed: {frame['message']}")
                return result
            except asyncio.CancelledError:
                self._cancel_input_watchdog(sid)
                runtime = self._runtimes.get(sid)
                if runtime:
                    await asyncio.shield(runtime.close(remove_base=False))
                if sid in self._sessions:
                    self._sessions[sid].update(running=False, screenshots=[], held_inputs={})
                raise
            except Exception as error:
                self._cancel_input_watchdog(sid)
                # An uncertain transport/input failure invalidates the live session.
                runtime = self._runtimes.get(sid)
                if runtime:
                    await runtime.close(remove_base=False)
                if sid in self._sessions:
                    self._sessions[sid].update(running=False, screenshots=[], held_inputs={},
                        last={"operation": operation, "success": False, "message": str(error)})
                return {"success": False, "message": str(error)}

    @environment_manager.action(name="start_game", read_only=False, destructive=False,
        description="Launch the selected native game in Docker, wait for its MCP bridge and capture a rendered frame.")
    async def start_game(self, scene: str = "", ctx=None, **kwargs):
        return await self._native("start", ctx, scene=scene)

    @environment_manager.action(name="stop_game", read_only=False, destructive=False,
        description="Stop the owned game and remove its transient runtime bridge; preserve authored files.")
    async def stop_game(self, ctx=None, **kwargs):
        return await self._native("stop", ctx)

    @environment_manager.action(name="observe", read_only=True, destructive=False,
        description="Capture the running game's actual PNG frame and retain a workspace artifact.")
    async def observe(self, ctx=None, **kwargs):
        return await self._native("observe", ctx)

    @environment_manager.action(name="press_key", read_only=False, destructive=False,
        description="Hold a player key (W, Right, Space etc.) for 1..2000 ms, release it and observe the resulting frame.")
    async def press_key(self, key: str, duration_ms: int = 300, ctx=None, **kwargs):
        return await self._native("key", ctx, key=key, duration_ms=duration_ms)

    @environment_manager.action(name="click", read_only=False, destructive=False,
        description="Click native viewport coordinates through Godot input and capture the resulting frame.")
    async def click(self, x: float, y: float, button: int = 1, ctx=None, **kwargs):
        return await self._native("click", ctx, x=x, y=y, button=button)

    @environment_manager.action(name="move_mouse", read_only=False, destructive=False,
        description="Move the native pointer/camera through player mouse input; relative=true sends a motion delta.")
    async def move_mouse(self, x: float, y: float, relative: bool = False, ctx=None, **kwargs):
        args = {"x": x, "y": y}
        if relative:
            args = {"x": 0, "y": 0, "relative_x": x, "relative_y": y}
        return await self._native("mouse", ctx, **args)

    @environment_manager.action(name="input_sequence", read_only=False, destructive=False,
        description="Run 1..32 ordered player-input steps (type, arguments), then capture a frame. Types: key_down/up (key or action), key_tap (key plus ctrl/shift/alt/meta/physical), text (text), click/double_click/mouse_down/up (x,y,button), mouse_move (x,y,relative_x,relative_y), drag (fromX,fromY,toX,toY,button,steps), scroll (x,y,direction,amount), gamepad (type=button|axis,index,value,device), touch (action=press|release|drag,x,y,index,toX,toY,steps), action_strength (actionName,strength), mouse_mode (mode), wait (duration_ms), wait_frames (frames,frameType), release_all. Default releases all controls at end. Set release_at_end=false to hold across calls; idle inputs auto-release after the configured lease. Whole sequence has a 15-second deadline; waits total <=10000 ms. No game-state mutation.")
    async def input_sequence(self, steps: InputSteps, release_at_end: bool = True, ctx=None, **kwargs):
        """Execute ordered player inputs.

        Args:
            steps: Input objects shaped as {"type":"key_tap","arguments":{"key":"Enter"}}. Put key, duration_ms and other parameters inside arguments; never beside type.
            release_at_end: Release held controls after the sequence; false keeps them until explicit release or the idle lease expires.
        """
        return await self._native("sequence", ctx, steps=steps, release_at_end=release_at_end)

    @environment_manager.action(name="press_keys", read_only=False, destructive=False,
        description="Hold up to eight gameplay keys together for 1..10000 ms, then release them and observe. For GUI shortcuts use input_sequence key_tap with modifier flags.")
    async def press_keys(self, keys: list[str], duration_ms: int = 300, ctx=None, **kwargs):
        if not 1 <= len(keys) <= 8 or not 1 <= duration_ms <= 10000:
            return {"success": False, "message": "Provide 1..8 keys and duration_ms in 1..10000"}
        steps = [{"type": "key_down", "arguments": {"key": key}} for key in dict.fromkeys(keys)]
        steps.append({"type": "wait", "arguments": {"duration_ms": duration_ms}})
        steps += [{"type": "key_up", "arguments": {"key": key}} for key in reversed(list(dict.fromkeys(keys)))]
        return await self.input_sequence(steps, release_at_end=False, ctx=ctx)

    @environment_manager.action(name="type_text", read_only=False, destructive=False,
        description="Send 1..256 Unicode codepoints to the focused native text control through keyboard events. Focus it by clicking first; this does not set a hidden UI property.")
    async def type_text(self, text: str, ctx=None, **kwargs):
        return await self.input_sequence([{"type": "text", "arguments": {"text": text}}], ctx=ctx)

    @environment_manager.action(name="release_inputs", read_only=False, destructive=False,
        description="Release all held keyboard, mouse, action, gamepad and touch inputs, then capture a fresh frame.")
    async def release_inputs(self, ctx=None, **kwargs):
        return await self.input_sequence([{"type": "release_all"}], ctx=ctx)

    @environment_manager.action(name="input_state", read_only=True, destructive=False,
        description="Inspect actual keyboard/action/mouse-button state and pointer mode without modifying gameplay state.")
    async def input_state(self, keys: list[str] | None = None, actions: list[str] | None = None,
                          mouse_buttons: list[int] | None = None, ctx=None, **kwargs):
        return await self._native("input_state", ctx, keys=keys or [], actions=actions or [], mouseButtons=mouse_buttons or [])

    @environment_manager.action(name="inspect_runtime", read_only=True, destructive=False,
        description="Read tree, ui, logs or errors from the running game. Introspection is diagnostic evidence, not player input.")
    async def inspect_runtime(self, kind: str = "tree", ctx=None, **kwargs):
        return await self._native("inspect", ctx, kind=kind)

    async def get_state(self, ctx=None, **kwargs):
        rec = self._sessions.get(self._sid(ctx))
        if rec is None:
            return {"success": True, "state": "Godot idle. Use prepare_workspace before Bash authoring, then doctor and open_project."}
        last = rec["last"]
        state = f"Godot project: {rec['project'] or '(none)'}\n"
        state += f"Last operation: {last.get('operation', '(none)')}; success={last.get('success')}; "
        state += f"status={last.get('status', '')}; exit={last.get('exit_code')}; log={last.get('log_path', '')}\n"
        state += str(last.get("output") or last.get("message") or "")[-2000:]
        state += f"\nBackend: {self.backend}; native game running={rec.get('running', False)}"
        runtime = self._runtimes.get(self._sid(ctx))
        bridge_error = rec.get("bridge_error") or getattr(runtime, "bridge_error", "")
        if bridge_error:
            rec["screenshots"] = []
            state += f"\nInteraction bridge unavailable: {bridge_error}. Running is the last known process state, not proof of a working connection."
        state += f"\nHeld inputs: {list(rec.get('held_inputs', {}))}; idle release after {self.input_idle_seconds}s"
        return {"success": True, "state": state,
                "extra": {"screenshots": rec.get("screenshots", [])}}

    async def close_session(self, session_id):
        sid = session_id or "default"
        async with self._locks.setdefault(sid, asyncio.Lock()):
            self._cancel_input_watchdog(sid)
            proc = self._processes.pop(sid, None)
            if proc is not None:
                await self._stop(proc)
            runtime = self._runtimes.pop(sid, None)
            if runtime is not None:
                if runtime.task and not runtime.task.done():
                    try:
                        await runtime.call("stop_project", {}, timeout=10)
                    except Exception:
                        pass
                await runtime.close()
            self._sessions.pop(sid, None)

    async def cleanup(self):
        for sid in list(self._sessions):
            await self.close_session(sid)
        self._locks.clear()
        self._project_locks.clear()
