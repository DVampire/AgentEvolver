"""Runtime discovery never starts a component or treats a draft name as loaded."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentevolver.environment.context import EnvironmentContextManager
from agentevolver.environment.server import EnvironmentManagerServer
from agentevolver.environment.types import ActionConfig, EnvironmentConfig
from agentevolver.connector.context import ConnectorContextManager
from agentevolver.connector.server import ConnectorManagerServer
from agentevolver.tool.default.observability import inspect as inspect_module


@pytest.fixture
def managers(tmp_path, monkeypatch):
    environments = EnvironmentManagerServer()
    environments.environment_context_manager = EnvironmentContextManager(base_dir=str(tmp_path / "env"))
    configs = environments.environment_context_manager._environment_configs
    for name in ("job", "browser_environment"):
        configs[name] = EnvironmentConfig(name=name, description=name, rules="Read the actual action schema.")
    configs["job"].actions["status"] = ActionConfig(
        env_name="job", name="status", description="Read job status",
        function_calling={"type": "function", "function": {"name": "status", "parameters": {
            "type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"],
        }}},
    )
    connectors = ConnectorManagerServer()
    connectors.connector_context_manager = ConnectorContextManager(base_dir=str(tmp_path / "connector"))
    registry = {"environment": environments, "connector": connectors,
                "workflow": SimpleNamespace(list=lambda: ["review_workflow"])}
    original = inspect_module.component_type_entry

    def entry(kind):
        value = original(kind)
        return replace(value, manager=lambda: registry[kind]) if kind in registry else value

    monkeypatch.setattr(inspect_module, "component_type_entry", entry)
    return environments, connectors


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [
    ("environment", ["job", "browser_environment"]), ("connector", []),
    ("workflow", ["review_workflow"]),
])
async def test_discovery_lists_loaded_names_including_an_empty_registry(managers, kind, expected):
    result = await inspect_module.InspectTool()(capability_type=kind)
    assert result.success and result.data == {"type": kind, "scope": "loaded", "available": expected}
    assert "does not load components" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,name,available", [
    ("environment", "factor_mining_environment", ["job", "browser_environment"]),
    ("connector", "equity_source_connector", []),
])
async def test_recorded_failed_lookups_explain_recovery_without_registering(managers, kind, name, available):
    result = await inspect_module.InspectTool()(capability_type=kind, name=name)
    assert not result.success
    assert result.data["error_code"] == "not_loaded"
    assert result.data["registered"] is False and result.data["available"] == available
    assert "register it first" in result.message
    assert await managers[0].list() == ["job", "browser_environment"]
    assert await managers[1].list() == []


@pytest.mark.asyncio
async def test_loaded_environment_still_returns_its_real_action_schema(managers):
    result = await inspect_module.InspectTool()(capability_type="environment", name="job")
    assert result.success and result.data["registered"]
    assert result.data["members"] == ["status"]
    contract = result.data["schema"]["status"]["function"]
    assert contract["name"] == "job__status"
    assert contract["parameters"]["required"] == ["job_id"]
    assert managers[0].environment_context_manager._environment_configs["job"].instance is None


@pytest.mark.asyncio
async def test_invalid_type_remains_an_error(managers):
    result = await inspect_module.InspectTool()(capability_type="not_a_type")
    assert not result.success and "Unknown capability_type" in result.message
