"""Interactive service facade over the existing AgentEvolver runtime."""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from argparse import Namespace

from dotenv import load_dotenv
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
from agentevolver.prompt import prompt_manager
from agentevolver.session.types import SessionContext
from agentevolver.skill import skill_manager
from agentevolver.task import TaskCategory, TaskPriority, TaskRecord, task_manager
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.utils import make_id
from agentevolver.version import version_manager
from agentevolver.tool import tool_manager


@dataclass
class GatewaySession:
    context: SessionContext
    created_at: str
    task_ids: list[str] = field(default_factory=list)
    capabilities: Dict[str, list[str]] = field(default_factory=dict)


class AgentGateway:
    """Owns interactive sessions and maps protocol commands to backend operations."""

    def __init__(self, *, event_history_size: int = 10_000) -> None:
        self._sessions: Dict[str, GatewaySession] = {}
        self._subscribers: set[asyncio.Queue[GatewayEvent]] = set()
        self._events: Dict[str, Deque[GatewayEvent]] = defaultdict(
            lambda: deque(maxlen=event_history_size)
        )
        self._sequence: Dict[str, int] = defaultdict(int)
        self._active_agent_tasks: Dict[str, asyncio.Task] = {}
        self._initialized = False
        self._stopping = False

    async def start(self, config_path: str, *, stdio: bool = False) -> None:
        """Initialize the configured runtime once for all Gateway sessions."""
        if self._initialized:
            return

        load_dotenv()
        config.initialize(config_path=config_path, args=Namespace(cfg_options=None), verbose=False)
        logger.initialize(config=config, console_stream=sys.stderr if stdio else None)

        await version_manager.initialize()
        await trace_manager.initialize()
        await trace_manager.start(start_server=False)
        trace_manager.subscribe(self._on_trace_event)
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
        await command_manager.initialize()
        await extension_manager.initialize()
        extension_manager.subscribe(self._on_extension_change)

        task_dir = os.path.join(config.run_dir, "gateway", "tasks")
        await task_manager.initialize(work_dir=task_dir, handler=self._run_task)
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
        await trace_manager.stop()
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
        workspace = params.get("workspace") or getattr(config, "workspace_dir", config.work_dir)
        context = SessionContext(
            id=session_id,
            name=params.get("name") or "interactive",
            work_dir=str(Path(workspace).expanduser().resolve()),
            extra={"workspace": str(Path(workspace).expanduser().resolve())},
        )
        session = GatewaySession(
            context=context,
            created_at=datetime.now(timezone.utc).isoformat(),
            capabilities=await self._available_capabilities(),
        )
        self._sync_session_capabilities(session)
        self._sessions[session_id] = session
        await self._publish("session.created", {"workspace": context.work_dir, "name": context.name}, session_id=session_id)
        return {"session_id": session_id, "workspace": context.work_dir}

    async def _command_session_list(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sessions": [
                {
                    "session_id": session_id,
                    "name": session.context.name,
                    "workspace": session.context.work_dir,
                    "task_ids": session.task_ids,
                }
                for session_id, session in self._sessions.items()
            ]
        }

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

    async def _command_capability_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(params.get("kind") or "")
        name = str(params.get("name") or "")
        managers = {
            "skills": skill_manager,
            "tools": tool_manager,
            "agents": agent_manager,
            "connectors": connector_manager,
            "environments": environment_manager,
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
                "document": self._command_document(command),
                "document_path": None,
                "language": "markdown",
            }
        manager = managers.get(kind)
        if manager is None:
            raise ValueError("kind must be one of: skills, tools, agents, connectors, environments, commands")
        if not name:
            raise ValueError("Capability name is required")
        if name not in await manager.list():
            raise ValueError(f"Unknown {kind}: {name}")

        info = await manager.get_info(name)
        if info is None:
            raise ValueError(f"Capability details are unavailable: {name}")

        if kind in {"tools", "agents"}:
            document, document_path, language = "", None, "schema"
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
            "document": document,
            "document_path": document_path,
            "language": language,
        }

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
                work_dir=session.context.work_dir,
                extra={"session_id": session_id, "capabilities": session.capabilities},
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

    async def _run_task(self, record: TaskRecord) -> Any:
        session_id = record.task.session_id
        if not session_id or session_id not in self._sessions:
            raise RuntimeError(f"Gateway session is unavailable for task {record.task.id}")
        session = self._sessions[session_id]
        self._sync_session_capabilities(session)
        await self._publish("task.started", {"content": record.task.content}, session_id=session_id, task_id=record.task.id)
        agent_task = asyncio.create_task(
            agent_manager(
                name="meta_agent",
                input={
                    "task": record.task.content,
                    "files": record.task.files,
                    "capabilities": session.capabilities,
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
    def _sync_session_capabilities(session: GatewaySession) -> None:
        session.context.extra["capabilities"] = session.capabilities
        for kind, context_key in {
            "tools": "tool_allowlist",
            "skills": "skill_allowlist",
            "agents": "agent_allowlist",
            "connectors": "connector_allowlist",
            "environments": "environment_allowlist",
        }.items():
            session.context.extra[context_key] = list(session.capabilities.get(kind, []))

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
