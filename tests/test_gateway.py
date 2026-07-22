"""Focused tests for the interactive Gateway's protocol-only behavior."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentevolver.gateway.protocol import GatewayCommand, PROTOCOL_VERSION
from agentevolver.gateway.service import AgentGateway
from agentevolver.model import model_manager
from agentevolver.model.types import ModelConfig


async def _test_event_ordering_and_replay() -> None:
    gateway = AgentGateway(event_history_size=10)
    queue = await gateway.subscribe()

    created = await gateway.handle(
        GatewayCommand(id="create", method="session.create", params={})
    )
    assert created.ok
    session_id = created.result["session_id"]
    assert isinstance(session_id, str)

    first = await queue.get()
    assert first.type == "session.created"
    assert first.seq_no == 1

    await gateway._publish("agent.progress", {"step": 1}, session_id=session_id)
    second = await queue.get()
    assert second.seq_no == 2

    replay = await gateway.handle(
        GatewayCommand(
            id="replay",
            method="session.events",
            params={"session_id": session_id, "after_seq": 1},
        )
    )
    assert replay.ok
    assert [event["type"] for event in replay.result["events"]] == ["agent.progress"]
    gateway.unsubscribe(queue)


def test_event_ordering_and_replay() -> None:
    asyncio.run(_test_event_ordering_and_replay())


def test_rejects_unknown_protocol_version() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        response = await gateway.handle(
            GatewayCommand(
                id="old",
                method="hello",
                params={},
                protocol_version=PROTOCOL_VERSION + 1,
            )
        )
        assert not response.ok
        assert response.error is not None
        assert response.error.code == "unsupported_protocol"

    asyncio.run(run())


def test_session_capability_selection_is_persisted() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        session_id = created.result["session_id"]

        catalog = await gateway.handle(GatewayCommand(id="catalog", method="capability.list"))
        assert catalog.ok
        selection = {
            kind: names[:1]
            for kind, names in catalog.result.items()
            if isinstance(names, list)
        }
        updated = await gateway.handle(
            GatewayCommand(
                id="select",
                method="session.capabilities.set",
                params={"session_id": session_id, "capabilities": selection},
            )
        )
        assert updated.ok
        assert updated.result["capabilities"] == selection

        invalid = await gateway.handle(
            GatewayCommand(
                id="invalid",
                method="session.capabilities.set",
                params={
                    "session_id": session_id,
                    "capabilities": {**selection, "tools": ["not-a-real-tool"]},
                },
            )
        )
        assert not invalid.ok
        assert invalid.error is not None
        assert invalid.error.code == "invalid_request"

    asyncio.run(run())


def test_session_can_be_renamed() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={"name": "web"})
        )
        assert created.ok
        session_id = created.result["session_id"]

        renamed = await gateway.handle(
            GatewayCommand(
                id="rename",
                method="session.rename",
                params={"session_id": session_id, "name": "Review authentication flow"},
            )
        )
        assert renamed.ok
        assert renamed.result["name"] == "Review authentication flow"

        listed = await gateway.handle(GatewayCommand(id="list", method="session.list"))
        assert listed.ok
        assert listed.result["sessions"][0]["name"] == "Review authentication flow"

    asyncio.run(run())


def test_extension_capability_changes_update_live_sessions() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        catalog = {"agents": [], "tools": [], "skills": [], "connectors": []}

        async def available_capabilities() -> dict[str, list[str]]:
            return {kind: list(names) for kind, names in catalog.items()}

        gateway._available_capabilities = available_capabilities  # type: ignore[method-assign]
        queue = await gateway.subscribe()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        session_id = created.result["session_id"]
        await queue.get()

        catalog["tools"] = ["generated_tool"]
        await gateway._on_extension_change(
            {"action": "registered", "module": "tool", "name": "generated_tool", "version": "1.0.0"}
        )

        selection_event = await queue.get()
        catalog_event = await queue.get()
        assert selection_event.type == "session.capabilities.updated"
        assert selection_event.session_id == session_id
        assert selection_event.payload["capabilities"]["tools"] == ["generated_tool"]
        assert catalog_event.type == "capabilities.changed"
        assert catalog_event.payload["capabilities"]["tools"] == ["generated_tool"]
        gateway.unsubscribe(queue)

    asyncio.run(run())


def test_commands_are_exposed_and_execute_in_a_gateway_session() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        catalog = await gateway.handle(GatewayCommand(id="catalog", method="capability.list"))
        assert catalog.ok
        assert "commands" in catalog.result
        assert "environments" in catalog.result
        assert "inspect" in catalog.result["commands"]

        detail = await gateway.handle(
            GatewayCommand(id="detail", method="capability.get", params={"kind": "commands", "name": "inspect"})
        )
        assert detail.ok
        assert detail.result["usage"] == "/inspect <type> <name>"
        assert detail.result["language"] == "markdown"
        assert "## Examples" in detail.result["document"]
        assert "`/inspect tool bash_tool`" in detail.result["document"]

        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        executed = await gateway.handle(
            GatewayCommand(
                id="help",
                method="command.execute",
                params={"session_id": created.result["session_id"], "raw": "/help"},
            )
        )
        assert executed.ok
        assert executed.result["success"] is True
        assert "[control]" in executed.result["message"]

    asyncio.run(run())


def test_model_catalog_groups_models_without_credentials() -> None:
    async def run() -> None:
        models = model_manager.model_context_manager.models
        previous_models = dict(models)
        try:
            models.clear()
            models["openai/demo"] = ModelConfig(
                model_name="openai/demo",
                model_id="demo-1",
                model_type="chat/completions",
                provider="openai",
                api_key="must-not-be-exposed",
                supports_functions=True,
            )
            response = await AgentGateway().handle(GatewayCommand(id="models", method="model.list"))
            assert response.ok
            assert response.result["providers"] == [{
                "name": "openai",
                "models": [{
                    "name": "openai/demo",
                    "id": "demo-1",
                    "type": "chat/completions",
                    "streaming": True,
                    "functions": True,
                    "vision": False,
                }],
            }]
        finally:
            models.clear()
            models.update(previous_models)

    asyncio.run(run())


def test_model_configuration_is_editable_without_exposing_api_keys() -> None:
    async def run() -> None:
        models = model_manager.model_context_manager.models
        previous_models = dict(models)
        registered: list[ModelConfig] = []

        async def register_model(_, config: ModelConfig) -> None:
            registered.append(config)

        try:
            models.clear()
            models["openai/demo"] = ModelConfig(
                model_name="openai/demo",
                model_id="demo-1",
                model_type="chat/completions",
                provider="openai",
                api_key="must-not-be-exposed",
            )
            gateway = AgentGateway()
            detail = await gateway.handle(
                GatewayCommand(id="model-detail", method="model.get", params={"name": "openai/demo"})
            )
            assert detail.ok
            assert detail.result["has_api_key"] is True
            assert "api_key" not in detail.result["configuration"]

            with patch.object(type(model_manager), "register_model", register_model):
                updated = await gateway.handle(
                    GatewayCommand(
                        id="model-configure",
                        method="model.configure",
                        params={
                            "original_name": "openai/demo",
                            "configuration": {"temperature": 0.2},
                        },
                    )
                )
            assert updated.ok
            assert registered[0].api_key == "must-not-be-exposed"
            assert registered[0].temperature == 0.2
            assert "api_key" not in updated.result["configuration"]
        finally:
            models.clear()
            models.update(previous_models)

    asyncio.run(run())


def test_session_files_upload_in_chunks_and_can_be_removed() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        session_id = created.result["session_id"]

        content = b"<html><body>Agent task</body></html>"
        begun = await gateway.handle(
            GatewayCommand(
                id="begin",
                method="file.upload.begin",
                params={"session_id": session_id, "name": "task.html", "size": len(content), "mime_type": "text/html"},
            )
        )
        assert begun.ok
        file_id = begun.result["file"]["id"]
        chunk = await gateway.handle(
            GatewayCommand(
                id="chunk",
                method="file.upload.chunk",
                params={"session_id": session_id, "file_id": file_id, "data": base64.b64encode(content).decode()},
            )
        )
        assert chunk.ok
        completed = await gateway.handle(
            GatewayCommand(id="complete", method="file.upload.complete", params={"session_id": session_id, "file_id": file_id})
        )
        assert completed.ok
        path = completed.result["file"]["path"]
        assert open(path, "rb").read() == content

        listed = await gateway.handle(GatewayCommand(id="list", method="file.list", params={"session_id": session_id}))
        assert listed.ok
        assert [item["name"] for item in listed.result["files"]] == ["task.html"]

        removed = await gateway.handle(
            GatewayCommand(id="remove", method="file.remove", params={"session_id": session_id, "file_id": file_id})
        )
        assert removed.ok
        assert not Path(path).exists()

    asyncio.run(run())


def test_session_rejects_client_selected_project_root() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        response = await gateway.handle(
            GatewayCommand(
                id="create",
                method="session.create",
                params={"project_root": "/tmp/not-server-managed"},
            )
        )
        assert not response.ok
        assert response.error is not None
        assert response.error.code == "invalid_request"

    asyncio.run(run())


def test_gateway_keeps_session_workspace_empty_and_separate_from_source(tmp_path: Path) -> None:
    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "project.txt").write_text("original", encoding="utf-8")
        gateway = AgentGateway(workspace_source=source)
        created = await gateway.handle(
            GatewayCommand(
                id="create",
                method="session.create",
                params={"workspace": str(source)},
            )
        )
        assert created.ok
        workspace = Path(created.result["workspace"])
        assert workspace != source
        # The session sandbox is lazily materialized (ProjectSandbox.create(materialize=False)):
        # opening a session must leave no empty workspace directory behind — it is created
        # on first real use, not up front.
        assert not workspace.exists()
        assert created.result["source_workspace"] == str(source)

        listed = await gateway.handle(GatewayCommand(id="list", method="session.list"))
        assert listed.ok
        assert listed.result["sessions"][0]["source_workspace"] == str(source)

    asyncio.run(run())


def test_gateway_rejects_arbitrary_client_workspace(tmp_path: Path) -> None:
    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(
                id="create",
                method="session.create",
                params={"workspace": str(source)},
            )
        )
        assert not created.ok
        assert created.error is not None
        assert created.error.code == "invalid_request"

    asyncio.run(run())


def test_workspace_browser_is_session_scoped_and_reads_text(tmp_path: Path) -> None:
    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        gateway = AgentGateway(workspace_source=source)
        created = await gateway.handle(GatewayCommand(id="create", method="session.create", params={}))
        session_id = created.result["session_id"]
        workspace = Path(created.result["workspace"])
        # Workspace is lazily materialized, so create the tree the way first real use would.
        (workspace / "src").mkdir(parents=True)
        (workspace / "src" / "app.py").write_text("print('sandbox')\n", encoding="utf-8")
        (workspace / ".secret").write_text("hidden", encoding="utf-8")

        tree = await gateway.handle(GatewayCommand(
            id="tree", method="workspace.tree", params={"session_id": session_id, "path": ""},
        ))
        assert tree.ok
        assert [entry["name"] for entry in tree.result["entries"]] == ["src"]

        nested = await gateway.handle(GatewayCommand(
            id="nested", method="workspace.tree", params={"session_id": session_id, "path": "src"},
        ))
        assert nested.result["entries"][0]["path"] == "src/app.py"

        opened = await gateway.handle(GatewayCommand(
            id="read", method="workspace.file.read",
            params={"session_id": session_id, "path": "src/app.py"},
        ))
        assert opened.ok
        assert opened.result["content"] == "print('sandbox')\n"
        assert opened.result["language"] == "python"
        assert len(opened.result["etag"]) == 64

        escaped = await gateway.handle(GatewayCommand(
            id="escape", method="workspace.file.read",
            params={"session_id": session_id, "path": "../source/.secret"},
        ))
        assert not escaped.ok
        assert escaped.error is not None
        assert escaped.error.code == "invalid_request"

    asyncio.run(run())


def test_session_exposes_staged_extension_sandbox() -> None:
    async def run() -> None:
        gateway = AgentGateway()
        created = await gateway.handle(
            GatewayCommand(id="create", method="session.create", params={})
        )
        assert created.ok
        assert created.result["extension_root"] == str(Path(created.result["project_root"]) / "extension")

        stage = await gateway.handle(
            GatewayCommand(
                id="stage",
                method="extension.stage.get",
                params={"session_id": created.result["session_id"]},
            )
        )
        assert stage.ok
        assert stage.result["staging"]["valid"] is True
        assert stage.result["staging"]["components"] == []
        assert stage.result["mounts"][:2] == [
            {"source": str(Path(created.result["project_root"]) / "workspace"), "target": "/workspace", "mode": "rw"},
            {"source": str(Path(created.result["project_root"]) / "extension"), "target": "/extension", "mode": "rw"},
        ]

    asyncio.run(run())


def test_tool_and_agent_details_are_human_readable_guides() -> None:
    guide = AgentGateway._capability_usage_document(
        "tools",
        "example_tool",
        SimpleNamespace(description="Perform an example action.", instruction="Run this only when needed."),
    )
    assert "## What it does" in guide
    assert "## How to use it" in guide
    assert "Run this only when needed." in guide
    assert '"properties"' not in guide
