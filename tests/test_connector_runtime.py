"""Agent-authored stdio servers retain schemas, effects and execution errors."""

import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from agentevolver.connector.context import ConnectorContextManager


@pytest.fixture
def local_server(tmp_path):
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "server.py").write_text('''from mcp.server.fastmcp import FastMCP
mcp = FastMCP("source-test")

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def fetch(fail: bool = False) -> str:
    if fail:
        raise ValueError("source request failed: HTTP 401")
    return "training snapshot received"

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True})
def bars() -> dict:
    return {"scope": "synthetic", "bars": [{"date": "2020-01-02", "close": 100.0}]}

if __name__ == "__main__":
    mcp.run(transport="stdio")
''')
    (folder / "CONNECTOR.md").write_text('''---
name: source_connector
description: Local connector transport fixture.
version: 1.0.0
type: worker
permission_mode: read_only
connection:
  transport: stdio
  command: python
  args: [server.py]
actions: [fetch, bars]
---
# Source
''')
    return folder


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_local_mcp_result_keeps_execution_status(tmp_path, local_server, fail):
    manager = ConnectorContextManager(
        base_dir=str(tmp_path / "run"), default_connectors_dir=str(tmp_path / "default"),
        extension_connectors_dir=str(tmp_path / "extensions"),
    )
    cfg = manager._parse_connector_dir(local_server)
    manager._connector_configs[cfg.name] = cfg
    try:
        # The generated manifest does not duplicate server-side effect annotations.
        # Its first native invocation must discover them before the permission check.
        assert cfg.action_annotations == {}
        result = await manager(name=cfg.name, action="fetch", input={"fail": fail})
        assert cfg.action_annotations["fetch"]["readOnlyHint"] is True
        assert result.success is not fail
        assert ("HTTP 401" if fail else "training snapshot received") in result.message
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_read_only_dataset_is_archived_by_framework_not_mcp(tmp_path, local_server):
    import hashlib
    manifest = local_server / "CONNECTOR.md"
    manifest.write_text(manifest.read_text().replace("permission_mode: read_only", "permission_mode: read_only\nresult_mode: artifact"))
    validator = runpy.run_path(str(Path(__file__).parents[1] / (
        "agentevolver/skill/evolving/self_evolving_skill/scripts/connector/validate.py"
    )))["validate_connector"]
    valid, message = validator(local_server)
    assert valid, message  # The authoring preflight must accept the runtime contract.
    manager = ConnectorContextManager(base_dir=str(tmp_path / "logs"))
    cfg = manager._parse_connector_dir(local_server)
    manager._connector_configs[cfg.name] = cfg
    try:
        result = await manager(name=cfg.name, action="bars", input={})
        assert result.success
        path = Path(result.data["artifact_path"])
        assert path.is_relative_to(tmp_path / "logs" / "results")
        body = path.read_bytes()
        assert hashlib.sha256(body).hexdigest() == result.data["sha256"]
        assert json.loads(body)["result"]["bars"][0]["close"] == 100.0
        assert "bars" not in json.loads(result.message)  # compact receipt, no array in prompt
        assert cfg.model_dump()["result_mode"] == "artifact"
        replay = await manager(name=cfg.name, action="bars", input={})
        assert replay.data["artifact_path"] == str(path)
        failed = await manager(name=cfg.name, action="fetch", input={"fail": True})
        assert not failed.success and len(list(path.parent.glob("*.json"))) == 1
    finally:
        await manager.cleanup()


def test_documented_probe_cli_discovers_local_server(local_server):
    script = Path(__file__).parents[1] / (
        "agentevolver/skill/evolving/self_evolving_skill/scripts/connector/probe.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "stdio", sys.executable, str(local_server / "server.py")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    tools = json.loads(result.stdout)["tools"]
    assert tools[0]["name"] == "fetch"
    assert tools[0]["input_schema"]["properties"]["fail"]["type"] == "boolean"
    assert tools[0]["annotations"]["readOnlyHint"] is True


@pytest.mark.parametrize("mode,valid", [("inline", True), ("artifact", True), ("file", False)])
def test_authoring_validator_and_runtime_agree_on_manifest_fields(tmp_path, local_server, mode, valid):
    import yaml
    validator = runpy.run_path(str(Path(__file__).parents[1] / (
        "agentevolver/skill/evolving/self_evolving_skill/scripts/connector/validate.py"
    )))["validate_connector"]
    path = local_server / "CONNECTOR.md"
    frontmatter = path.read_text().split("---", 2)[1]
    fields = yaml.safe_load(frontmatter)
    fields.update(result_mode=mode, action_descriptions={"bars": "Historical rows"},
                  action_annotations={"bars": {"readOnlyHint": True, "openWorldHint": True}})
    path.write_text("---\n" + yaml.safe_dump(fields) + "---\n# Source\n")
    accepted, message = validator(local_server)
    assert accepted is valid, message
    manager = ConnectorContextManager(base_dir=str(tmp_path / "logs"))
    if valid:
        cfg = manager._parse_connector_dir(local_server)
        assert cfg.result_mode == mode and cfg.action_annotations["bars"]["readOnlyHint"]
    else:
        with pytest.raises(ValueError):
            manager._parse_connector_dir(local_server)


@pytest.mark.asyncio
async def test_artifact_write_failure_is_not_a_successful_download(tmp_path, local_server, monkeypatch):
    manager = ConnectorContextManager(base_dir=str(tmp_path / "logs"))
    cfg = manager._parse_connector_dir(local_server)
    cfg.result_mode = "artifact"
    manager._connector_configs[cfg.name] = cfg

    def failed_commit(*args):
        raise OSError("Disk full while committing saved result")

    monkeypatch.setattr("agentevolver.connector.context.os.replace", failed_commit)
    try:
        result = await manager(name=cfg.name, action="bars", input={})
        assert not result.success and "Disk full" in result.message
        assert not list((tmp_path / "logs" / "results").iterdir())
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_mcp_error_returns_to_the_agent_for_candidate_repair(tmp_path, local_server, monkeypatch):
    """Exercise the loop/transport with controlled decisions, without paid model calls."""
    import shutil
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentevolver.agent.loop import Agent, ActionCall, ActionResult, Decision, ToolRouter
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.message.types import SystemMessage, ToolMessage

    monkeypatch.setattr("agentevolver.connector.context.version_manager", SimpleNamespace(
        get_version=AsyncMock(return_value=None), register_version=AsyncMock(),
    ))
    manager = ConnectorContextManager(
        base_dir=str(tmp_path / "run"), default_connectors_dir=str(tmp_path / "default"),
        extension_connectors_dir=str(tmp_path / "extensions"),
    )
    source = local_server / "server.py"
    working_source = source.read_text().replace("source request failed: HTTP 401", "parser not implemented")
    source.write_text(working_source.replace("if fail:", "if True:"))
    await manager.register(str(local_server), version="1.0.0", enable_evolving=True)
    candidate = tmp_path / "repaired_source"
    outcomes = []

    class RepairRouter(ToolRouter):
        async def schemas(self, agent, ctx):
            names = ["source_connector__fetch", "write_candidate", "register_candidate"]
            return [], {name: ("fixture", name) for name in names}

        async def invoke(self, call, **kwargs):
            if call.name == "source_connector__fetch":
                response = await manager(name="source_connector", action="fetch", input={})
                result = CapabilityRouter._from_response(call, response)
            elif call.name == "write_candidate":
                shutil.copytree(local_server, candidate)
                (candidate / "server.py").write_text(working_source)
                result = ActionResult(call, output=str(candidate))
            else:
                cfg = await manager.register(str(candidate), override=True, version="1.0.1")
                result = ActionResult(call, output=cfg.version)
            outcomes.append(result)
            return result

    class RepairAgent(Agent):
        async def system_messages(self, ctx):
            return [SystemMessage(content="Repair the fixture MCP and retry its operation.")]

        def project_context(self, ctx):
            return ""

        async def think(self, step, live=()):
            _, self._routing = await self.router.schemas(self, self.ctx)
            steps = ["source_connector__fetch", "write_candidate", "register_candidate",
                     "source_connector__fetch"]
            if step == len(steps):
                return Decision(text="The repaired MCP operation succeeded.")
            return Decision(calls=[ActionCall(id=f"repair-{step}", name=steps[step])])

    agent = RepairAgent(name="mcp_repair_probe", router=RepairRouter(), max_step=6)
    try:
        result = await agent("Repair the missing parser and complete the original operation")
        assert result.success and agent.step == 4
        assert len(outcomes) == 4
        assert "parser not implemented" in outcomes[0].error and not outcomes[0].final
        assert outcomes[-1].ok and outcomes[-1].output == "training snapshot received"
        assert (await manager.get("source_connector")).version == "1.0.1"
        assert "if True:" in source.read_text()  # The failed version was preserved.
        replies = [m for m in agent.conversation.items if isinstance(m, ToolMessage)]
        assert replies[0].is_error and "parser not implemented" in replies[0].content
        assert not replies[-1].is_error
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_failed_discovery_keeps_the_cause_and_can_be_retried(tmp_path, monkeypatch):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from agentevolver.connector.types import ConnectorConfig

    manager = ConnectorContextManager(base_dir=str(tmp_path))
    cfg = ConnectorConfig(name="broken", connector_dir=str(tmp_path / "source"),
                          connection={"transport": "stdio", "command": sys.executable},
                          actions=["fetch"], permission_mode="read_only")
    manager._connector_configs[cfg.name] = cfg
    attempts = []

    async def get_tools(self, **kwargs):
        attempts.append(True)
        raise ExceptionGroup("MCP startup failed", [RuntimeError("missing parser dependency")])

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", get_tools)
    for _ in range(2):
        result = await manager(name=cfg.name, action="fetch", input={})
        assert not result.success
        assert "missing parser dependency" in result.message
        assert cfg.connector_dir in result.message
    assert len(attempts) == 2  # A startup failure does not permanently poison discovery.
