"""Focused tests for the interactive Gateway's protocol-only behavior."""

from __future__ import annotations

import asyncio
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
        GatewayCommand(id="create", method="session.create", params={"workspace": "."})
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
            GatewayCommand(id="create", method="session.create", params={"workspace": "."})
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
            GatewayCommand(id="create", method="session.create", params={"workspace": ".", "name": "web"})
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
            GatewayCommand(id="create", method="session.create", params={"workspace": "."})
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
            GatewayCommand(id="create", method="session.create", params={"workspace": "."})
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
