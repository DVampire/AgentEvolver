"""Session-scoped Godot projects with bounded, auditable editor CLI operations.

The engine runs beside the local workspace tools. This is not a remote editor or
a gameplay observer: browser_environment owns visual play of exported Web builds.
No binary is started during construction, initialization, or state observation.
"""

import asyncio
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
from typing import Any, Dict
import uuid

from pydantic import Field, PrivateAttr

from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import ENVIRONMENT
from agentevolver.session import isolated_workspace_root, resolve_workspace_root


@ENVIRONMENT.register_module(force=True)
class GodotEnvironment(Environment):
    name: str = Field(default="godot_environment")
    description: str = Field(default=(
        "Godot 4 project workspace: engine discovery, imports, GDScript parsing, "
        "bounded headless runs, exports and diagnostic logs. Visual play uses the browser."
    ))
    metadata: Dict[str, Any] = Field(default={"has_vision": False, "engine": "godot"})
    enable_evolving: bool = Field(default=False)
    binary_path: str = Field(default="")
    max_command_seconds: int = Field(default=300, ge=10, le=600)
    _sessions: dict = PrivateAttr(default_factory=dict)
    _locks: dict = PrivateAttr(default_factory=dict)
    _project_locks: dict = PrivateAttr(default_factory=dict)
    _processes: dict = PrivateAttr(default_factory=dict)

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
        if os.environ.get("AGENTEVOLVER_EXEC_CONTAINER", "").strip():
            raise ValueError(
                "GodotEnvironment requires the local execution backend. The configured Bash "
                "container would use a different filesystem; a shared-container adapter is required."
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
                    await asyncio.wait_for(proc.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    timed_out = True
                except asyncio.CancelledError:
                    cancelled = True
                    raise
            finally:
                await asyncio.shield(self._stop(proc))
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
            except (OSError, ValueError) as error:
                failure = {"success": False, "message": str(error)}
                if sid in self._sessions:
                    self._sessions[sid]["last"] = {"operation": operation, **failure}
                return failure

    @environment_manager.action(
        name="doctor", read_only=False, destructive=False,
        description="Discover the configured local Godot 4 editor and record its exact version. Does not install anything or validate export templates.",
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
        description="Parse ONE .gd file with --script --check-only. Use project-relative, res:// or absolute paths. This does not validate all project scripts or gameplay.",
    )
    async def check_script(self, script: str, timeout: int = 60, ctx=None, **kwargs):
        return await self._perform("check_script", ctx, timeout, script=script)

    @environment_manager.action(
        name="run_headless", read_only=False, destructive=False,
        description="Run the main scene, a scene, or a SceneTree/MainLoop script without a window. Bounded by engine iterations AND wall time. A clean exit is not an assertion pass or visual play evidence.",
    )
    async def run_headless(self, scene: str = "", script: str = "", frames: int = 120,
                           timeout: int = 60, ctx=None, **kwargs):
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

    async def get_state(self, ctx=None, **kwargs):
        rec = self._sessions.get(self._sid(ctx))
        if rec is None:
            return {"success": True, "state": "Godot idle. No engine started. Use doctor, then open_project."}
        last = rec["last"]
        state = f"Godot project: {rec['project'] or '(none)'}\n"
        state += f"Last operation: {last.get('operation', '(none)')}; success={last.get('success')}; "
        state += f"status={last.get('status', '')}; exit={last.get('exit_code')}; log={last.get('log_path', '')}\n"
        state += str(last.get("output") or last.get("message") or "")[-2000:]
        return {"success": True, "state": state}

    async def close_session(self, session_id):
        sid = session_id or "default"
        async with self._locks.setdefault(sid, asyncio.Lock()):
            proc = self._processes.pop(sid, None)
            if proc is not None:
                await self._stop(proc)
            self._sessions.pop(sid, None)

    async def cleanup(self):
        for sid in list(self._sessions):
            await self.close_session(sid)
        self._locks.clear()
        self._project_locks.clear()
