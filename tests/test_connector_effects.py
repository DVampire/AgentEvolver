from types import SimpleNamespace

import pytest

from agentevolver.connector.context import ConnectorContextManager
from agentevolver.connector.types import ConnectorConfig
from agentevolver.response.types import Response, ResponseType


def _manager(tmp_path):
    return ConnectorContextManager(
        base_dir=str(tmp_path / "run"),
        default_connectors_dir=str(tmp_path / "default"),
        extension_connectors_dir=str(tmp_path / "extension"),
    )


def test_mcp_effect_annotations_are_normalized_from_sdk_aliases(tmp_path):
    manager = _manager(tmp_path)
    tool = SimpleNamespace(
        name="lookup", description="read a record", args_schema={"type": "object"},
        annotations=SimpleNamespace(model_dump=lambda **kwargs: {
            "read_only_hint": True, "open_world_hint": False,
        }),
    )

    _, _, _, annotations = manager._contract_from_tools([tool])
    assert annotations == {
        "lookup": {"readOnlyHint": True, "openWorldHint": False},
    }


def test_frontmatter_effect_annotations_are_normalized(tmp_path):
    manager = _manager(tmp_path)
    connector_dir = tmp_path / "records"
    connector_dir.mkdir()
    (connector_dir / "CONNECTOR.md").write_text(
        "---\nname: records\nactions: [lookup]\naction_annotations:\n"
        "  lookup:\n    read_only_hint: true\n    destructive_hint: false\n---\n",
        encoding="utf-8",
    )

    config = manager._parse_connector_dir(connector_dir)

    assert config.action_annotations == {
        "lookup": {"readOnlyHint": True, "destructiveHint": False},
    }


@pytest.mark.asyncio
async def test_read_only_mcp_action_runs_without_approval(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    manager._connector_configs["records"] = ConnectorConfig(
        name="records", version="1", actions=["lookup"],
        action_annotations={"lookup": {"readOnlyHint": True}},
    )
    called = []

    async def invoke(config, action, args):
        called.append((action, args))
        return Response(type=ResponseType.CONNECTOR, success=True, message="ok")

    monkeypatch.setattr(manager, "_invoke_mcp", invoke)
    response = await manager(name="records", action="lookup", input={"id": 1})

    assert response.success and called == [("lookup", {"id": 1})]


@pytest.mark.asyncio
async def test_unknown_mcp_effect_fails_closed_without_approval(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    manager._connector_configs["records"] = ConnectorConfig(
        name="records", version="1", actions=["update"],
    )
    called = False

    async def invoke(config, action, args):
        nonlocal called
        called = True
        return Response(type=ResponseType.CONNECTOR, success=True, message="bad")

    monkeypatch.setattr(manager, "_invoke_mcp", invoke)
    response = await manager(name="records", action="update", input={"id": 1})

    assert not response.success and not called
    assert response.extra["execution"]["error_code"] == "approval_unavailable"
