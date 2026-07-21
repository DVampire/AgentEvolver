"""Interactive service facade over the existing AgentEvolver runtime."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from argparse import Namespace

from dotenv import load_dotenv
from lxml import etree, html as lxml_html
from agentevolver.agent import agent_manager
from agentevolver.command import command_manager
from agentevolver.command.types import CommandContext
from agentevolver.config import config
from agentevolver.connector import connector_manager
from agentevolver.environment import environment_manager
from agentevolver.extension import extension_manager
from agentevolver.gateway.protocol import (
    PROTOCOL_VERSION,
    GatewayCommand,
    GatewayEvent,
    GatewayResponse,
    error_response,
)
from agentevolver.hook import hook_manager
from agentevolver.logger import logger
from agentevolver.memory import memory_manager
from agentevolver.model import model_manager
from agentevolver.model.types import ModelConfig
from agentevolver.prompt import prompt_manager
from agentevolver.session.types import SessionContext
from agentevolver.session.project import bind_session_roots
from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.skill import skill_manager
from agentevolver.task import TaskCategory, TaskPriority, TaskRecord, task_manager
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.utils import make_id
from agentevolver.version import version_manager
from agentevolver.tool import tool_manager
from agentevolver.workflow import workflow_manager
from agentevolver.deploy import deployment_manager
from agentevolver.environment.stream import environment_stream


@dataclass
class GatewaySession:
    context: SessionContext
    created_at: str
    sandbox: ProjectSandbox
    task_ids: list[str] = field(default_factory=list)
    capabilities: Dict[str, list[str]] = field(default_factory=dict)
    uploads: Dict[str, "GatewayUpload"] = field(default_factory=dict)


@dataclass
class GatewayUpload:
    id: str
    name: str
    path: str
    size: int
    mime_type: str = "application/octet-stream"
    received: int = 0
    completed: bool = False

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "mime_type": self.mime_type,
            "completed": self.completed,
        }


class AgentGateway:
    """Owns interactive sessions and maps protocol commands to backend operations."""

    def __init__(
        self,
        *,
        event_history_size: int = 10_000,
        workspace_source: Optional[str | Path] = None,
    ) -> None:
        self._sessions: Dict[str, GatewaySession] = {}
        self._subscribers: set[asyncio.Queue[GatewayEvent]] = set()
        self._events: Dict[str, Deque[GatewayEvent]] = defaultdict(
            lambda: deque(maxlen=event_history_size)
        )
        self._sequence: Dict[str, int] = defaultdict(int)
        self._active_agent_tasks: Dict[str, asyncio.Task] = {}
        # Session whose roots the shared runtime is currently bound to (see
        # _bind_runtime_to_session); None until the first task runs.
        self._bound_session_id: Optional[str] = None
        self._initialized = False
        self._stopping = False
        self._workspace_source = (
            Path(workspace_source).expanduser().resolve() if workspace_source else None
        )

    _MAX_UPLOAD_SIZE = 100 * 1024 * 1024
    _MAX_UPLOAD_CHUNK_SIZE = 1024 * 1024
    _MAX_WORKSPACE_FILE_SIZE = 2 * 1024 * 1024
    # Media is returned base64 (≈33% larger on the wire), so keep the cap modest.
    _MAX_WORKSPACE_MEDIA_SIZE = 25 * 1024 * 1024
    _MEDIA_MIME_PREFIXES = ("image/", "audio/", "video/")
    _MEDIA_MIME_TYPES = frozenset({"application/pdf"})
    _MAX_WORKSPACE_ENTRIES = 500

    async def start(self, config_path: str, *, stdio: bool = False) -> None:
        """Initialize the configured runtime once for all Gateway sessions."""
        if self._initialized:
            return

        load_dotenv()
        config.initialize(config_path=config_path, args=Namespace(cfg_options=None), verbose=False)
        extension_manager.set_base_dir(config.extension_root)
        # No session exists yet, so do not open a tag-level log file — the file sink
        # is attached to the session's own log root by _bind_runtime_to_session.
        logger.initialize(config=config, console_stream=sys.stderr if stdio else None, file_logging=False)

        await version_manager.initialize()
        await trace_manager.initialize()
        await trace_manager.start()
        trace_manager.subscribe(self._on_trace_event)
        environment_stream.subscribe(self._on_environment_view)
        await trajectory_manager.initialize()
        await hook_manager.initialize()
        await model_manager.initialize()
        await prompt_manager.initialize(prompt_names=getattr(config, "prompt_names", None))
        await memory_manager.initialize(memory_names=getattr(config, "memory_names", None))
        await tool_manager.initialize(tool_names=getattr(config, "tool_names", None))
        await skill_manager.initialize(skill_names=getattr(config, "skill_names", None))
        await connector_manager.initialize(connector_names=getattr(config, "connector_names", None))
        env_names = getattr(config, "env_names", None)
        if env_names:
            await environment_manager.initialize(env_names=env_names)
        await agent_manager.initialize(agent_names=getattr(config, "agent_names", None))
        await workflow_manager.initialize(workflow_names=getattr(config, "workflow_names", None))
        # Deploy registry is project-global (under the .agentevolver home), so init it
        # once here — before any session binds — and reconcile sites still serving.
        await deployment_manager.initialize()
        await command_manager.initialize()
        await extension_manager.initialize()
        extension_manager.subscribe(self._on_extension_change)

        task_dir = os.path.join(config.log_root, "gateway", "tasks")
        await task_manager.initialize(log_root=task_dir, handler=self._run_task)
        await task_manager.start(num_workers=1)
        self._initialized = True
        await self._publish("gateway.ready", {"protocol_version": PROTOCOL_VERSION})

    async def stop(self) -> None:
        if not self._initialized or self._stopping:
            return
        self._stopping = True
        for task in tuple(self._active_agent_tasks.values()):
            task.cancel()
        await asyncio.gather(*self._active_agent_tasks.values(), return_exceptions=True)
        self._active_agent_tasks.clear()
        await task_manager.stop()
        extension_manager.unsubscribe(self._on_extension_change)
        trace_manager.unsubscribe(self._on_trace_event)
        environment_stream.unsubscribe(self._on_environment_view)
        await trace_manager.stop()
        await workflow_manager.cleanup()
        await agent_manager.cleanup()
        await command_manager.cleanup()
        self._initialized = False

    async def handle(self, command: GatewayCommand) -> GatewayResponse:
        if command.protocol_version != PROTOCOL_VERSION:
            return error_response(
                command.id,
                "unsupported_protocol",
                f"Expected protocol version {PROTOCOL_VERSION}",
            )
        try:
            handler = getattr(self, f"_command_{command.method.replace('.', '_')}", None)
            if handler is None:
                return error_response(command.id, "unknown_method", f"Unknown method: {command.method}")
            result = await handler(command.params)
            return GatewayResponse(id=command.id, ok=True, result=result or {})
        except ValueError as exc:
            return error_response(command.id, "invalid_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"| ❌ Gateway command {command.method} failed: {exc}", exc_info=True)
            return error_response(command.id, "internal_error", str(exc))

    async def subscribe(self) -> asyncio.Queue[GatewayEvent]:
        queue: asyncio.Queue[GatewayEvent] = asyncio.Queue(maxsize=2_000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[GatewayEvent]) -> None:
        self._subscribers.discard(queue)

    async def _command_hello(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "transports": ["stdio", "websocket"],
            "sessions": len(self._sessions),
        }

    async def _command_session_create(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = params.get("session_id") or make_id()
        if session_id in self._sessions:
            raise ValueError(f"Session already exists: {session_id}")
        if params.get("project_root") is not None:
            raise ValueError("project_root is server-managed; create a session without this parameter")
        configured_project_root = getattr(config, "project_root", None)
        default_project_root = Path(configured_project_root) if configured_project_root else Path.cwd() / "output"
        project_root = default_project_root / session_id
        # Clients open a session as soon as they connect; most never run anything.
        # Only describe the sandbox here — it is created on first real use so idle
        # sessions leave no empty directory behind.
        sandbox = ProjectSandbox.create(
            project_root, shared_extension_root=Path(config.extension_root), materialize=False,
        )
        requested_workspace = params.get("workspace")
        requested_source = Path(requested_workspace).expanduser().resolve() if requested_workspace else None
        if requested_source and (
            self._workspace_source is None or requested_source != self._workspace_source
        ):
            raise ValueError("workspace must match the server-controlled workspace source")
        source_workspace = str(self._workspace_source) if self._workspace_source else None
        context = SessionContext(
            id=session_id,
            name=params.get("name") or "interactive",
            workspace_root=str(sandbox.workspace_root),
            extra={
                "workspace": str(sandbox.workspace_root),
                **sandbox.describe(),
                "gateway_session": True,
                "sandbox_mounts": sandbox.mounts(),
                "source_workspace": source_workspace,
            },
        )
        session = GatewaySession(
            context=context,
            created_at=datetime.now(timezone.utc).isoformat(),
            sandbox=sandbox,
            capabilities=await self._available_capabilities(),
        )
        self._sync_session_capabilities(session)
        self._sessions[session_id] = session
        payload = {"workspace": context.workspace_root, "project_root": str(sandbox.project_root), "extension_root": str(sandbox.extension_root), "name": context.name, "source_workspace": source_workspace}
        await self._publish("session.created", payload, session_id=session_id)
        return {"session_id": session_id, **payload, "sandbox": sandbox.describe(), "mounts": sandbox.mounts()}

    async def _command_session_list(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sessions": [
                {
                    "session_id": session_id,
                    "name": session.context.name,
                    "workspace": session.context.workspace_root,
                    "source_workspace": session.context.extra.get("source_workspace"),
                    "project_root": str(session.sandbox.project_root),
                    "extension_root": str(session.sandbox.extension_root),
                    "task_ids": session.task_ids,
                }
                for session_id, session in self._sessions.items()
            ]
        }

    async def _command_extension_stage_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        sandbox = self._sessions[session_id].sandbox
        try:
            validation = sandbox.validate()
            validation["valid"] = True
        except ValueError as exc:
            validation = {"valid": False, "error": str(exc), "components": sandbox.staged_components()}
        return {"sandbox": sandbox.describe(), "mounts": sandbox.mounts(), "staging": validation}

    async def _command_extension_promote(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        sandbox = self._sessions[session_id].sandbox
        before = extension_manager.read_manifest()
        report = sandbox.promote(
            overwrite=bool(params.get("overwrite", False)),
            relative_paths=params.get("relative_paths"),
        )
        registered: list[Dict[str, str]] = []
        try:
            for component in report["promoted"]:
                module = component["module"]
                config_data = {"enable_evolving": True} if module != "prompt" else None
                name = await extension_manager.add_component(
                    module, component["destination"], config=config_data,
                    run_smoke=params.get("run_smoke"),
                )
                registered.append({"module": module, "name": name, "path": component["destination"]})
        except Exception:
            for item in reversed(registered):
                try:
                    await extension_manager.unload(item["module"], item["name"])
                except Exception as rollback_error:  # continue restoring the batch
                    logger.error(
                        f"| ❌ Failed to unload partial promotion "
                        f"{item['module']}:{item['name']}: {rollback_error}",
                        exc_info=True,
                    )
            try:
                sandbox.rollback_promotion(report)
            finally:
                # add_component may have unloaded the failing live component before
                # raising. Restore the exact pre-transaction active set even when
                # filesystem cleanup itself reports an error.
                await extension_manager.restore_manifest(before)
            raise
        sandbox.mark_promotion(report, "committed")
        payload = {**report, "registered": registered}
        await self._publish("extension.promoted", payload, session_id=session_id)
        return payload

    async def _command_session_rename(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("Session name is required")
        if len(name) > 100:
            raise ValueError("Session name must be at most 100 characters")
        session = self._sessions[session_id]
        session.context.name = name
        await self._publish("session.renamed", {"name": name}, session_id=session_id)
        return {"session_id": session_id, "name": name}

    async def _command_session_events(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        after_seq = int(params.get("after_seq", 0))
        return {
            "events": [event.model_dump(mode="json") for event in self._events[session_id] if event.seq_no > after_seq]
        }

    async def _command_task_submit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        content = str(params.get("content") or "").strip()
        if not content:
            raise ValueError("Task content is required")
        files = [str(item) for item in params.get("files", [])]
        workspace = Path(self._sessions[session_id].context.workspace_root).resolve()
        for path in files:
            try:
                Path(path).expanduser().resolve().relative_to(workspace)
            except ValueError as exc:
                raise ValueError("Task files must be located inside the session workspace") from exc
        # Bind before submitting so the queue persists this task under the session.
        self._bind_runtime_to_session(self._sessions[session_id])
        task_id = await task_manager.submit(
            content=content,
            category=TaskCategory.USER,
            priority=TaskPriority.HIGH,
            files=files,
            metadata={"source": "gateway"},
            session_id=session_id,
        )
        self._sessions[session_id].task_ids.append(task_id)
        await self._publish("task.submitted", {"content": content, "files": files}, session_id=session_id, task_id=task_id)
        return {"task_id": task_id}

    async def _command_file_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._sessions[self._require_session_id(params)]
        return {"files": [upload.public() for upload in session.uploads.values() if upload.completed]}

    def _workspace_path(self, session: GatewaySession, value: Any) -> tuple[Path, str]:
        """Resolve a client relative path without permitting cross-session access."""
        raw = str(value or "").replace("\\", "/")
        if raw.startswith("/"):
            raise ValueError("Workspace paths must be relative")
        relative = raw.strip("/")
        parts = Path(relative).parts
        if ".." in parts:
            raise ValueError("Workspace paths must be relative and may not contain '..'")
        root = session.sandbox.workspace_root.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Workspace path escapes the current session") from exc
        return candidate, relative

    async def _command_workspace_tree(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """List one workspace directory; clients expand the tree lazily."""
        session = self._sessions[self._require_session_id(params)]
        directory, relative = self._workspace_path(session, params.get("path"))
        if not directory.exists() and not relative:
            # Session opened but nothing has run yet, so its workspace does not exist
            # on disk. Report it as empty rather than an error.
            return {"path": relative, "entries": [], "truncated": False}
        if not directory.exists() or not directory.is_dir():
            raise ValueError(f"Workspace directory was not found: {relative or '.'}")
        show_hidden = bool(params.get("show_hidden", False))
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if not show_hidden and child.name.startswith("."):
                continue
            # Do not expose symlinks that resolve beyond this session workspace.
            try:
                child.resolve().relative_to(session.sandbox.workspace_root.resolve())
            except ValueError:
                continue
            stat = child.stat()
            child_relative = child.relative_to(session.sandbox.workspace_root).as_posix()
            entries.append({
                "name": child.name,
                "path": child_relative,
                "kind": "directory" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
                "modified_at": stat.st_mtime,
            })
            if len(entries) >= self._MAX_WORKSPACE_ENTRIES:
                break
        return {
            "path": relative,
            "entries": entries,
            "truncated": len(entries) >= self._MAX_WORKSPACE_ENTRIES,
        }

    async def _command_workspace_file_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Read a bounded workspace file: UTF-8 text, or base64 for media.

        Images, audio, video and PDFs are returned base64-encoded (``encoding``
        says which) so the client can render them directly from a data URL,
        rather than being rejected as "binary".
        """
        session = self._sessions[self._require_session_id(params)]
        path, relative = self._workspace_path(session, params.get("path"))
        if not relative or not path.exists() or not path.is_file():
            raise ValueError(f"Workspace file was not found: {relative or '.'}")
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        is_media = mime_type.startswith(self._MEDIA_MIME_PREFIXES) or mime_type in self._MEDIA_MIME_TYPES

        size = path.stat().st_size
        limit = self._MAX_WORKSPACE_MEDIA_SIZE if is_media else self._MAX_WORKSPACE_FILE_SIZE
        if size > limit:
            raise ValueError(
                f"Workspace file is larger than the {limit // (1024 * 1024)} MB preview limit"
            )
        raw = path.read_bytes()

        if is_media:
            content = base64.b64encode(raw).decode("ascii")
            encoding = "base64"
            language = "plaintext"
        else:
            if b"\x00" in raw[:8192]:
                raise ValueError("Binary files cannot be opened in the text preview")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("File is not valid UTF-8 text") from exc
            encoding = "utf-8"
            mime_type = mime_type or "text/plain"
            language = self._workspace_language(path.suffix.lower())

        return {
            "path": relative,
            "name": path.name,
            "content": content,
            "encoding": encoding,
            "size": size,
            "modified_at": path.stat().st_mtime,
            "etag": hashlib.sha256(raw).hexdigest(),
            "mime_type": mime_type,
            "language": language,
        }

    @staticmethod
    def _workspace_language(suffix: str) -> str:
        return {
            ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".html": "html", ".htm": "html",
            ".css": "css", ".scss": "scss", ".json": "json", ".yaml": "yaml",
            ".yml": "yaml", ".md": "markdown", ".mdx": "markdown", ".sh": "shell",
            ".bash": "shell", ".zsh": "shell", ".toml": "ini", ".ini": "ini",
            ".xml": "xml", ".sql": "sql", ".dockerfile": "dockerfile",
        }.get(suffix, "plaintext")

    async def _command_file_upload_begin(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._sessions[self._require_session_id(params)]
        name = self._safe_upload_name(params.get("name"))
        size = params.get("size")
        if not isinstance(size, int) or size < 0:
            raise ValueError("size must be a non-negative integer")
        if size > self._MAX_UPLOAD_SIZE:
            raise ValueError("File exceeds the 2 GB upload limit")
        mime_type = str(params.get("mime_type") or "application/octet-stream")[:255]
        upload_id = make_id()
        # An upload is real work in this session — create its roots before writing.
        session.sandbox.materialize()
        upload_dir = Path(session.context.workspace_root) / "uploads" / session.context.id
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"{upload_id}_{name}"
        path.touch(exist_ok=False)
        upload = GatewayUpload(id=upload_id, name=name, path=str(path), size=size, mime_type=mime_type)
        session.uploads[upload_id] = upload
        return {"file": upload.public()}

    async def _command_file_upload_chunk(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._sessions[self._require_session_id(params)]
        upload = self._require_upload(session, params)
        if upload.completed:
            raise ValueError("Upload is already complete")
        encoded = params.get("data")
        if not isinstance(encoded, str):
            raise ValueError("data must be base64 text")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("data must be valid base64 text") from exc
        if len(chunk) > self._MAX_UPLOAD_CHUNK_SIZE:
            raise ValueError("Upload chunk exceeds the 1 MB limit")
        if upload.received + len(chunk) > upload.size:
            raise ValueError("Upload contains more data than the declared file size")
        with Path(upload.path).open("ab") as file:
            file.write(chunk)
        upload.received += len(chunk)
        return {"file_id": upload.id, "received": upload.received, "size": upload.size}

    async def _command_file_upload_complete(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._sessions[self._require_session_id(params)]
        upload = self._require_upload(session, params)
        if upload.received != upload.size:
            raise ValueError(f"Upload is incomplete ({upload.received} of {upload.size} bytes received)")
        upload.completed = True
        await self._publish("file.uploaded", {"file": upload.public()}, session_id=session.context.id)
        return {"file": upload.public()}

    async def _command_file_remove(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._sessions[self._require_session_id(params)]
        upload = self._require_upload(session, params)
        Path(upload.path).unlink(missing_ok=True)
        session.uploads.pop(upload.id, None)
        return {"file_id": upload.id, "removed": True}

    @staticmethod
    def _safe_upload_name(value: Any) -> str:
        name = Path(str(value or "")).name
        name = re.sub(r"[^A-Za-z0-9._()\- ]", "_", name).strip(" .")
        if not name:
            raise ValueError("A valid file name is required")
        return name[:180]

    @staticmethod
    def _require_upload(session: GatewaySession, params: Dict[str, Any]) -> GatewayUpload:
        upload_id = str(params.get("file_id") or "")
        upload = session.uploads.get(upload_id)
        if upload is None:
            raise ValueError("Unknown uploaded file")
        return upload

    async def _command_task_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(params.get("task_id") or "")
        if not task_id:
            raise ValueError("task_id is required")
        active = self._active_agent_tasks.get(task_id)
        if active is not None:
            active.cancel()
            return {"task_id": task_id, "cancelled": True}
        cancelled = await task_manager.cancel(task_id)
        return {"task_id": task_id, "cancelled": cancelled}

    async def _command_capability_list(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return await self._available_capabilities()

    async def _command_workflow_run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Launch a registered workflow without blocking the interactive Gateway."""
        session_id = self._require_session_id(params)
        name = str(params.get("name") or "")
        if not name:
            raise ValueError("name is required")
        run_id = workflow_manager.start(
            name, input=params.get("input") or {}, ctx=self._sessions[session_id].context,
        )
        return {"run_id": run_id, "state": "created"}

    async def _command_workflow_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(params.get("run_id") or "")
        run = workflow_manager.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown workflow run: {run_id}")
        return run.model_dump(mode="json")

    async def _command_workflow_pause(self, params: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(params.get("run_id") or "")
        return {"run_id": run_id, "accepted": workflow_manager.pause(run_id)}

    async def _command_workflow_continue(self, params: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(params.get("run_id") or "")
        return {"run_id": run_id, "accepted": workflow_manager.continue_run(run_id)}

    async def _command_workflow_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(params.get("run_id") or "")
        return {"run_id": run_id, "accepted": workflow_manager.cancel(run_id)}

    async def _command_workflow_restore(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        name, checkpoint = str(params.get("name") or ""), str(params.get("checkpoint") or "")
        if not name or not checkpoint:
            raise ValueError("name and checkpoint are required")
        run = await workflow_manager.resume(
            name, checkpoint, ctx=self._sessions[session_id].context,
        )
        return run.model_dump(mode="json")

    async def _command_capability_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(params.get("kind") or "")
        name = str(params.get("name") or "")
        return await self._capability_detail(kind, name)

    # ------------------------------------------------------------------
    # Deploy — project-global registry of sites/services the agents deployed
    # ------------------------------------------------------------------

    async def _command_deploy_list(self, _: Dict[str, Any]) -> Dict[str, Any]:
        """Return every deployed site (project-global; survives sessions/restarts)."""
        sites = await deployment_manager.list_sites()
        return {"sites": [site.model_dump(mode="json") for site in sites]}

    async def _command_port_list(self, _: Dict[str, Any]) -> Dict[str, Any]:
        """Return the central port registry (framework host + env + deploy ports)."""
        from agentevolver.port import port_manager
        return {"ports": port_manager.list()}

    async def _command_deploy_redeploy(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Rebuild a stopped/detached site from its stored request (new URL likely)."""
        site_id = str(params.get("site_id") or "")
        if not site_id:
            raise ValueError("site_id is required")
        site = await deployment_manager.redeploy(site_id)
        await self._publish("deploy.changed", {"action": "redeploy", "site_id": site_id})
        return site.model_dump(mode="json")

    async def _command_deploy_stop(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Stop a running site; its record stays for later redeploy."""
        site_id = str(params.get("site_id") or "")
        if not site_id:
            raise ValueError("site_id is required")
        site = await deployment_manager.stop_site(site_id)
        await self._publish("deploy.changed", {"action": "stop", "site_id": site_id})
        return site.model_dump(mode="json")

    @staticmethod
    def _workflow_preview_document(source: str) -> str:
        """Build a self-contained preview for a sandboxed browser iframe.

        Persisted Workflows may reference only the framework's shared visual assets.
        A ``srcdoc`` iframe has no useful base URL for those repository-relative paths,
        so the Gateway embeds the trusted CSS and renderer without changing the stored
        executable HTML.
        """
        visual_dir = Path(__file__).resolve().parents[1] / "visual"
        assets = {
            "prompt.css": (visual_dir / "css" / "prompt.css").read_text(encoding="utf-8"),
            "workflow.css": (visual_dir / "css" / "workflow.css").read_text(encoding="utf-8"),
            "workflow.js": (visual_dir / "js" / "workflow.js").read_text(encoding="utf-8"),
        }
        document = lxml_html.document_fromstring(source)
        for link in document.xpath("//link[@rel='stylesheet']"):
            filename = Path(link.get("href", "")).name
            if filename not in assets:
                continue
            link.tag = "style"
            link.attrib.clear()
            link.text = assets[filename]
        for script in document.xpath("//script[@src]"):
            filename = Path(script.get("src", "")).name
            if filename != "workflow.js":
                continue
            script.attrib.clear()
            script.text = assets[filename]
        rendered = etree.tostring(document, encoding="unicode", method="html")
        return "<!DOCTYPE html>\n" + rendered

    async def _capability_detail(self, kind: str, name: str) -> Dict[str, Any]:
        managers = {
            "skills": skill_manager,
            "tools": tool_manager,
            "agents": agent_manager,
            "connectors": connector_manager,
            "environments": environment_manager,
        }
        if kind == "workflows":
            if not name or name not in workflow_manager.list():
                raise ValueError(f"Unknown workflows: {name}")
            spec = workflow_manager.get(name)
            assert spec is not None
            properties = {
                input_name: dict(input_spec.parameter_schema or {"type": input_spec.type})
                for input_name, input_spec in spec.inputs.items()
            }
            required = [input_name for input_name, input_spec in spec.inputs.items() if input_spec.required]
            parameter_schema: Dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            }
            if required:
                parameter_schema["required"] = required
            document_path = None
            if spec.source_path:
                _, document_path = self._read_repository_file(
                    Path(__file__).resolve().parents[2], Path(spec.source_path),
                )
            return {
                "kind": kind,
                "name": name,
                "description": spec.description,
                "version": spec.version,
                "permission_mode": "workspace_write",
                "type": "dynamic_html",
                "enable_evolving": spec.enable_evolving,
                "actions": [],
                "parameter_schema": parameter_schema,
                "usage": f"Run workflow {name} with an input object.",
                "configuration": {},
                "editable": False,
                "document": spec.source,
                "preview_document": self._workflow_preview_document(spec.source),
                "document_path": document_path,
                "language": "html",
            }
        if kind == "commands":
            command = await command_manager.get(name)
            if command is None:
                raise ValueError(f"Unknown commands: {name}")
            return {
                "kind": kind,
                "name": name,
                "description": str(command.description),
                "version": "1.0.0",
                "permission_mode": str(command.permission_mode),
                "type": command.type.value,
                "enable_evolving": False,
                "actions": [],
                "parameter_schema": {
                    "type": "object",
                    "properties": {"args": {"type": "array", "items": {"type": "string"}}},
                    "required": [],
                },
                "usage": command.usage or f"/{command.name}",
                "configuration": {},
                "editable": False,
                "document": self._command_document(command),
                "document_path": None,
                "language": "markdown",
            }
        manager = managers.get(kind)
        if manager is None:
            raise ValueError("kind must be one of: skills, tools, agents, connectors, environments, workflows, commands")
        if not name:
            raise ValueError("Capability name is required")
        if name not in await manager.list():
            raise ValueError(f"Unknown {kind}: {name}")

        info = await manager.get_info(name)
        if info is None:
            raise ValueError(f"Capability details are unavailable: {name}")

        if kind in {"tools", "agents"}:
            document, document_path, language = self._capability_usage_document(kind, name, info), None, "markdown"
        else:
            document, document_path, language = self._capability_document(kind, name, info)
        return {
            "kind": kind,
            "name": name,
            "description": str(getattr(info, "description", "")),
            "version": str(getattr(info, "version", "1.0.0")),
            "permission_mode": str(getattr(info, "permission_mode", "workspace_write")),
            "type": getattr(info, "type", None),
            "enable_evolving": bool(getattr(info, "enable_evolving", False)),
            "actions": list(getattr(info, "actions", []) or []),
            "parameter_schema": self._parameter_schema(info),
            "usage": None,
            "configuration": self._capability_configuration(kind, info),
            "editable": kind in {"tools", "skills", "agents"},
            "document": document,
            "document_path": document_path,
            "language": language,
        }

    async def _command_capability_configure(self, params: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(params.get("kind") or "")
        name = str(params.get("name") or "")
        configuration = params.get("configuration")
        if kind not in {"tools", "skills", "agents"}:
            raise ValueError("Only tools, skills, and agents have editable configuration")
        if not name:
            raise ValueError("Capability name is required")
        if not isinstance(configuration, dict):
            raise ValueError("configuration must be an object")

        if kind == "tools":
            info = await tool_manager.get_info(name)
            if info is None:
                raise ValueError(f"Unknown tools: {name}")
            tool_class = getattr(info, "cls", None) or type(getattr(info, "instance", None))
            if tool_class is type(None):
                raise ValueError(f"Tool configuration is unavailable: {name}")
            await tool_manager.update(name, tool=tool_class, config=configuration)
        elif kind == "agents":
            info = await agent_manager.get_info(name)
            if info is None or getattr(info, "cls", None) is None:
                raise ValueError(f"Agent configuration is unavailable: {name}")
            await agent_manager.update(agent_cls=info.cls, agent_config_dict=configuration)
        else:
            info = await skill_manager.get_info(name)
            if info is None:
                raise ValueError(f"Unknown skills: {name}")
            allowed = {"description", "metadata", "content"}
            unknown = set(configuration) - allowed
            if unknown:
                raise ValueError(f"Unsupported skill configuration fields: {', '.join(sorted(unknown))}")
            if "metadata" in configuration and not isinstance(configuration["metadata"], dict):
                raise ValueError("configuration.metadata must be an object")
            if "description" in configuration and not isinstance(configuration["description"], str):
                raise ValueError("configuration.description must be a string")
            if "content" in configuration and not isinstance(configuration["content"], str):
                raise ValueError("configuration.content must be a string")
            await skill_manager.update(name, **configuration)

        detail = await self._capability_detail(kind, name)
        await self._publish(
            "capability.configured",
            {"kind": kind, "name": name, "version": detail["version"]},
        )
        return detail

    async def _command_model_list(self, _: Dict[str, Any]) -> Dict[str, Any]:
        providers: Dict[str, list[Dict[str, Any]]] = {}
        for model_name in model_manager.list():
            model = model_manager.get_model_config(model_name)
            if model is None:
                continue
            providers.setdefault(model.provider, []).append(self._model_summary(model))
        return {
            "providers": [
                {"name": provider, "models": sorted(models, key=lambda model: model["name"])}
                for provider, models in sorted(providers.items())
            ]
        }

    async def _command_model_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = str(params.get("name") or "")
        if not name:
            raise ValueError("Model name is required")
        model = model_manager.get_model_config(name)
        if model is None:
            raise ValueError(f"Unknown model: {name}")
        return {
            "model": self._model_summary(model),
            "configuration": self._safe_model_configuration(model),
            "has_api_key": bool(model.api_key),
        }

    async def _command_model_configure(self, params: Dict[str, Any]) -> Dict[str, Any]:
        original_name = str(params.get("original_name") or "").strip()
        configuration = params.get("configuration")
        if not isinstance(configuration, dict):
            raise ValueError("configuration must be an object")

        allowed_fields = {
            "model_name", "model_type", "model_id", "provider", "api_base",
            "temperature", "reasoning", "plugins", "max_completion_tokens",
            "max_output_tokens", "supports_streaming", "supports_functions",
            "supports_vision", "output_version", "timeout", "key_pool_name",
            "fallback_model",
        }
        unknown = set(configuration) - allowed_fields
        if unknown:
            raise ValueError(f"Unsupported model configuration fields: {', '.join(sorted(unknown))}")

        existing = model_manager.get_model_config(original_name) if original_name else None
        if original_name and existing is None:
            raise ValueError(f"Unknown model: {original_name}")

        merged = existing.model_dump() if existing is not None else {}
        merged.update(configuration)
        try:
            model = ModelConfig.model_validate(merged)
        except Exception as exc:  # pydantic exposes useful validation details
            raise ValueError(f"Invalid model configuration: {exc}") from exc

        if not model.model_name.strip() or not model.model_id.strip():
            raise ValueError("model_name and model_id must not be empty")
        if model.provider not in {"openai", "openrouter", "anthropic", "google"}:
            raise ValueError(f"Unsupported provider: {model.provider}")
        conflicting = model_manager.get_model_config(model.model_name)
        if conflicting is not None and model.model_name != original_name:
            raise ValueError(f"A model named {model.model_name} is already registered")

        supplied_key = params.get("api_key")
        if supplied_key is not None and not isinstance(supplied_key, str):
            raise ValueError("api_key must be a string")
        if bool(params.get("clear_api_key")):
            model.api_key = None
        elif isinstance(supplied_key, str) and supplied_key.strip():
            model.api_key = supplied_key.strip()

        await model_manager.register_model(model)
        if original_name and original_name != model.model_name:
            await model_manager.unregister_model(original_name)

        action = "updated" if existing is not None else "created"
        await self._publish("models.changed", {"action": action, "model": self._model_summary(model)})
        return {
            "model": self._model_summary(model),
            "configuration": self._safe_model_configuration(model),
            "has_api_key": bool(model.api_key),
        }

    @staticmethod
    def _model_summary(model: ModelConfig) -> Dict[str, Any]:
        return {
            "name": model.model_name,
            "id": model.model_id,
            "type": model.model_type,
            "streaming": model.supports_streaming,
            "functions": model.supports_functions,
            "vision": model.supports_vision,
        }

    @staticmethod
    def _safe_model_configuration(model: ModelConfig) -> Dict[str, Any]:
        return model.model_dump(exclude={"api_key"})

    async def _command_session_capabilities_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._sessions[self._require_session_id(params)]
        return {"capabilities": session.capabilities}

    async def _command_session_capabilities_set(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        requested = params.get("capabilities")
        if not isinstance(requested, dict):
            raise ValueError("capabilities must be an object")

        available = await self._available_capabilities()
        selection: Dict[str, list[str]] = {}
        for kind, names in available.items():
            requested_names = requested.get(kind, available[kind])
            if not isinstance(requested_names, list) or not all(isinstance(name, str) for name in requested_names):
                raise ValueError(f"capabilities.{kind} must be a list of names")
            invalid = set(requested_names) - set(names)
            if invalid:
                raise ValueError(f"Unknown {kind}: {', '.join(sorted(invalid))}")
            selection[kind] = list(dict.fromkeys(requested_names))

        session = self._sessions[session_id]
        session.capabilities = selection
        self._sync_session_capabilities(session)
        await self._publish("session.capabilities.updated", {"capabilities": selection}, session_id=session_id)
        return {"capabilities": selection}

    async def _command_command_execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = self._require_session_id(params)
        raw = str(params.get("raw") or "").strip()
        command_name = raw.lstrip("/").split(maxsplit=1)[0] if raw else ""
        if not command_name:
            raise ValueError("Command is required")
        session = self._sessions[session_id]
        if command_name not in {"help", "?"} and command_name not in session.capabilities.get("commands", []):
            raise ValueError(f"Command /{command_name} is disabled for this session")
        response = await command_manager.dispatch(
            raw,
            ctx=CommandContext(
                id=session_id,
                name=command_name,
                raw=raw,
                workspace_root=session.context.workspace_root,
                extra={
                    **session.context.extra,
                    "session_id": session_id,
                    "capabilities": session.capabilities,
                },
            ),
        )
        payload = {
            "raw": raw,
            "success": bool(response.success),
            "message": response.message,
            "data": response.data,
        }
        await self._publish("command.executed", payload, session_id=session_id)
        return payload

    async def _command_approval_respond(self, params: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(params.get("approval_id") or "")
        if not approval_id:
            raise ValueError("approval_id is required")
        # The existing runtime does not yet expose a user-approval rendezvous.
        # Accepting the command keeps the protocol stable while permissions migrate here.
        await self._publish("approval.responded", dict(params), session_id=params.get("session_id"))
        return {"approval_id": approval_id, "accepted": True}

    def _bind_runtime_to_session(self, session: GatewaySession) -> None:
        """Point the shared runtime at ``session``'s own project roots.

        Direct entry points (``examples/run_*``, the CLI) bind their session before
        any manager initializes, so every manager derives session-scoped paths from
        config.  The Gateway cannot: it initializes one shared runtime at startup,
        before any session exists.  Binding here — on the single, serialized task
        path (the task queue runs one worker) — gives the same result: config roots
        and the managers that persist run output all move under
        ``output/<tag>/<session-id>/`` for the duration of this task.
        """
        if self._bound_session_id == session.context.id:
            return
        # Work is about to happen in this session — create its roots now.
        session.sandbox.materialize()
        bind_session_roots(config, session.sandbox)
        # Managers cached their base_dir at initialize(); re-point the ones that
        # persist per-run output. Writers that read config at write time (the
        # snapshot hook) follow the rebound config automatically.
        trace_manager.rebind(config.log_root)
        memory_manager.rebind(config.log_root)
        trajectory_manager.rebind(config.log_root)
        task_manager.rebind(os.path.join(config.log_root, "tasks"))
        # Boot ran without a file sink (no session existed yet); attach it now so the
        # run log lands in this session too.
        logger.rebind(config.log_path)
        self._bound_session_id = session.context.id
        logger.info(f"| 📁 Runtime bound to session {session.context.id}: {config.log_root}")

    async def _run_task(self, record: TaskRecord) -> Any:
        session_id = record.task.session_id
        if not session_id or session_id not in self._sessions:
            raise RuntimeError(f"Gateway session is unavailable for task {record.task.id}")
        session = self._sessions[session_id]
        self._bind_runtime_to_session(session)
        self._sync_session_capabilities(session)
        await self._publish("task.started", {"content": record.task.content}, session_id=session_id, task_id=record.task.id)
        agent_task = asyncio.create_task(
            agent_manager(
                name="meta_agent",
                input={
                    "task": record.task.content,
                    "files": record.task.files,
                    "capabilities": session.capabilities,
                    "task_id": record.task.id,
                },
                ctx=session.context,
            ),
            name=f"gateway-agent-{record.task.id}",
        )
        self._active_agent_tasks[record.task.id] = agent_task
        try:
            response = await agent_task
            payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else {"result": str(response)}
            await self._publish("task.completed", payload, session_id=session_id, task_id=record.task.id)
            return response
        except asyncio.CancelledError:
            await self._publish("task.cancelled", {}, session_id=session_id, task_id=record.task.id)
            raise
        except Exception as exc:
            await self._publish("task.failed", {"error": str(exc)}, session_id=session_id, task_id=record.task.id)
            raise
        finally:
            self._active_agent_tasks.pop(record.task.id, None)

    async def _on_trace_event(self, event) -> None:
        payload = event.to_dict()
        await self._publish("trace.event", payload, session_id=event.session_id, task_id=event.task_id)

    async def _on_environment_view(self, view) -> None:
        """Republish an environment live-view to the client watching the active session.

        Tasks run serially, so the environment producing this view belongs to the
        currently bound session — scope the event to it (the view's own session_id
        is a sub-agent id the browser client would not match on).
        """
        payload = view.model_dump(mode="json")
        await self._publish("environment.view", payload, session_id=self._bound_session_id)

    async def _on_extension_change(self, change: Dict[str, str]) -> None:
        """Publish registry changes so connected clients see evolved components immediately."""
        kind_by_module = {
            "tool": "tools",
            "agent": "agents",
            "skill": "skills",
            "connector": "connectors",
            "environment": "environments",
        }
        kind = kind_by_module.get(change.get("module", ""))
        name = change.get("name")
        if not kind or not name:
            return

        available = await self._available_capabilities()
        action = change.get("action", "updated")
        is_available = name in available[kind]
        for session_id, session in self._sessions.items():
            current = [entry for entry in session.capabilities.get(kind, []) if entry in available[kind]]
            if action == "registered" and is_available:
                updated = list(dict.fromkeys([*current, name]))
            else:
                updated = current
            session.capabilities[kind] = updated
            self._sync_session_capabilities(session)
            await self._publish(
                "session.capabilities.updated",
                {"capabilities": session.capabilities},
                session_id=session_id,
            )

        await self._publish(
            "capabilities.changed",
            {
                "action": action,
                "kind": kind,
                "name": name,
                "version": change.get("version"),
                "capabilities": available,
            },
        )

    async def _publish(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> GatewayEvent:
        key = session_id or "_gateway"
        self._sequence[key] += 1
        event = GatewayEvent(
            type=event_type,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            seq_no=self._sequence[key],
        )
        self._events[key].append(event)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow clients reconnect and recover through session.events.
                self._subscribers.discard(queue)
        return event

    def _require_session_id(self, params: Dict[str, Any]) -> str:
        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        if session_id not in self._sessions:
            raise ValueError(f"Unknown session: {session_id}")
        return session_id

    async def _available_capabilities(self) -> Dict[str, list[str]]:
        return {
            "agents": await agent_manager.list(),
            "tools": await tool_manager.list(),
            "skills": await skill_manager.list(),
            "connectors": await connector_manager.list(),
            "environments": await environment_manager.list(),
            "workflows": workflow_manager.list(),
            "commands": await command_manager.list(),
        }

    def _capability_document(self, kind: str, name: str, info: Any) -> tuple[str, Optional[str], str]:
        repository_root = Path(__file__).resolve().parents[2]
        if kind == "skills":
            path = Path(str(getattr(info, "skill_dir", ""))) / "SKILL.md"
            content, relative_path = self._read_repository_file(repository_root, path)
            return content or str(getattr(info, "content", "")), relative_path, "markdown"
        if kind == "connectors":
            path = Path(str(getattr(info, "connector_dir", ""))) / "CONNECTOR.md"
            content, relative_path = self._read_repository_file(repository_root, path)
            return content or str(getattr(info, "content", "")), relative_path, "markdown"
        if kind == "environments":
            env_class = getattr(info, "cls", None)
            source_file = getattr(env_class, "__source_file__", None)
            if not source_file and env_class is not None:
                try:
                    from inspect import getfile
                    source_file = getfile(env_class)
                except (TypeError, OSError):
                    source_file = None
            if source_file:
                path = Path(str(source_file)).parent / "ENVIRONMENT.md"
                content, relative_path = self._read_repository_file(repository_root, path)
                if content:
                    return content, relative_path, "markdown"
            return str(getattr(info, "rules", "")), None, "markdown"

        source_path = getattr(info, "path", None)
        if source_path:
            content, relative_path = self._read_repository_file(repository_root, Path(str(source_path)))
            if content:
                return content, relative_path, "python"
        code = getattr(info, "code", None)
        if code:
            return str(code), None, "python"

        directory = "tool" if kind == "tools" else "agent"
        basename = name.removesuffix("_tool") if kind == "tools" else name
        matches = sorted((repository_root / "agentevolver" / directory).rglob(f"{basename}.py"))
        for path in matches:
            content, relative_path = self._read_repository_file(repository_root, path)
            if content:
                return content, relative_path, "python"
        return self._fallback_document(kind, name, info), None, "markdown"

    @staticmethod
    def _capability_usage_document(kind: str, name: str, info: Any) -> str:
        """Create a readable guide for callable capabilities, never a raw schema dump."""
        description = str(getattr(info, "description", "") or "No description is available.")
        instruction = str(getattr(info, "instruction", "") or "").strip()
        label = "tool" if kind == "tools" else "agent"
        lines = [
            "## What it does",
            description,
            "",
            "## How to use it",
        ]
        if instruction:
            lines.append(instruction)
        elif kind == "tools":
            lines.append(f"Enable **{name}** for the session. AgentEvolver calls it when the task requires this action.")
        else:
            lines.append(f"Enable **{name}** for the session. AgentEvolver can delegate suitable work to this specialist agent.")
        lines.extend([
            "",
            "## Session availability",
            f"Use the toggle in the {label} list to allow or disallow it for this session.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _sync_session_capabilities(session: GatewaySession) -> None:
        session.context.extra["capabilities"] = session.capabilities
        for kind, context_key in {
            "tools": "tool_allowlist",
            "skills": "skill_allowlist",
            "agents": "agent_allowlist",
            "connectors": "connector_allowlist",
            "environments": "environment_allowlist",
            "workflows": "workflow_allowlist",
        }.items():
            session.context.extra[context_key] = list(session.capabilities.get(kind, []))

    @staticmethod
    def _capability_configuration(kind: str, info: Any) -> Dict[str, Any]:
        if kind in {"tools", "agents"}:
            configuration = getattr(info, "config", None)
            return dict(configuration) if isinstance(configuration, dict) else {}
        if kind == "skills":
            metadata = getattr(info, "metadata", None)
            return {
                "description": str(getattr(info, "description", "")),
                "metadata": dict(metadata) if isinstance(metadata, dict) else {},
                "content": str(getattr(info, "content", "")),
            }
        return {}

    @staticmethod
    def _command_document(command: Any) -> str:
        """Render human-facing command help instead of exposing transport schemas."""
        usage = str(getattr(command, "usage", "") or f"/{command.name}")
        examples = [f"`{usage.split()[0]}`"]
        argument_values = {
            "type": "tool",
            "name": "bash_tool",
            "version": "1.0.0",
            "label": "before-evolution",
            "new_name": "my_copy",
            "goal...": "Improve reliability for invalid input.",
        }
        expanded = []
        for token in usage.split():
            required = token.startswith("<") and token.endswith(">")
            optional = token.startswith("[") and token.endswith("]")
            if not required and not optional:
                expanded.append(token)
                continue
            key = token[1:-1]
            value = argument_values.get(key)
            if value:
                expanded.append(value)
        full_example = " ".join(expanded)
        if full_example and full_example != usage.split()[0]:
            examples.append(f"`{full_example}`")

        return "\n".join([
            "## What it does",
            str(getattr(command, "description", "No description is available.")),
            "",
            "## Usage",
            f"`{usage}`",
            "",
            "## Run it",
            "Enter an enabled command in the chat composer and press Enter. Commands run against the current session.",
            "",
            "## Examples",
            *[f"- {example}" for example in examples],
        ])

    @staticmethod
    def _parameter_schema(info: Any) -> Optional[Dict[str, Any]]:
        function_calling = getattr(info, "function_calling", None)
        if isinstance(function_calling, dict):
            parameters = function_calling.get("parameters")
            if isinstance(parameters, dict):
                return parameters
        args_schema = getattr(info, "args_schema", None)
        if args_schema is not None and hasattr(args_schema, "model_json_schema"):
            try:
                schema = args_schema.model_json_schema()
                return schema if isinstance(schema, dict) else None
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _fallback_document(kind: str, name: str, info: Any) -> str:
        description = str(getattr(info, "description", "No description is available."))
        instruction = str(getattr(info, "instruction", ""))
        return f"# {name}\n\n**Kind:** {kind}\n\n{description}\n" + (f"\n## Instructions\n\n{instruction}\n" if instruction else "")

    @staticmethod
    def _read_repository_file(repository_root: Path, path: Path) -> tuple[str, Optional[str]]:
        try:
            resolved = path.expanduser().resolve()
            relative_path = resolved.relative_to(repository_root)
            if not resolved.is_file():
                return "", None
            return resolved.read_text(encoding="utf-8")[:200_000], str(relative_path)
        except (OSError, UnicodeDecodeError, ValueError):
            return "", None
