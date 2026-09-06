"""The agent and host must address the same mounted files, including plan.md."""

from pathlib import Path

import pytest

from agentevolver.paths import P, path_manager
from agentevolver.paths.server import PathManagerServer
from agentevolver.plan.server import PlanManagerServer, read_plan
from agentevolver.sandbox.default.base import OpenSandbox, to_host_path
from agentevolver.sandbox.default.docker import DockerSandbox
from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.sandbox.types import SandboxConfig


@pytest.fixture
def external_shell(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENTEVOLVER_EXEC_CONTAINER", "task-container")
    monkeypatch.setenv("AGENTEVOLVER_TASK_WORKSPACE", str(workspace))
    monkeypatch.setenv("AGENTEVOLVER_EXEC_WORKDIR", "/workspace")
    return workspace


def test_plan_uses_container_path_but_reads_host_file(external_shell):
    path_manager.bind_session("local", "plan-mount")
    path_manager.override(P.SESSION_WORKSPACE, external_shell)
    manager = PlanManagerServer()
    context = manager.context("plan-mount", enabled=True)
    assert 'path="/workspace/plan.md"' in context
    assert str(external_shell) not in context
    assert not (external_shell / "plan.md").exists()
    (external_shell / "plan.md").write_text("First implementation")
    assert "First implementation" in manager.context("plan-mount", enabled=True)
    (external_shell / "plan.md").write_text("Feedback received: revised implementation")
    assert "revised implementation" in manager.context("plan-mount", enabled=True)
    assert read_plan("plan-mount") == "Feedback received: revised implementation"
    assert path_manager.get(P.SESSION_PLAN) == external_shell / "plan.md"


@pytest.mark.asyncio
async def test_agent_workspace_prompt_matches_plan_path(external_shell):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agentevolver.agent.actor.meta_agent import MetaAgent

    path_manager.bind_session("local", "prompt-mount")
    path_manager.override(P.SESSION_WORKSPACE, external_shell)
    agent = MetaAgent()
    agent.router = SimpleNamespace(schemas=AsyncMock(return_value=([], {})))
    modules = await agent.prompt_modules(SimpleNamespace(id="prompt-mount", extra={}))
    assert modules["workspace_root"] == "/workspace"
    assert modules["shared_extension_root"] == str(path_manager.get(P.EXTENSION))


def test_projection_does_not_invent_mounts(external_shell, tmp_path):
    manager = PathManagerServer()
    assert manager.execution_path(external_shell / "nested" / "file") == Path("/workspace/nested/file")
    outside = tmp_path / "workspace-other" / "plan.md"
    assert manager.execution_path(outside) == outside
    (external_shell / "escape").symlink_to(tmp_path, target_is_directory=True)
    escaped = external_shell / "escape" / "plan.md"
    assert manager.execution_path(escaped) == escaped


def test_projection_needs_an_external_shell(external_shell, monkeypatch):
    monkeypatch.delenv("AGENTEVOLVER_EXEC_CONTAINER")
    assert path_manager.execution_path(external_shell / "plan.md") == external_shell / "plan.md"


def test_projection_honors_custom_workdir(external_shell, monkeypatch):
    monkeypatch.setenv("AGENTEVOLVER_EXEC_WORKDIR", "/work/task")
    assert path_manager.execution_path(external_shell / "plan.md") == Path("/work/task/plan.md")
    assert OpenSandbox(SandboxConfig(workdir="/work/task")).container_workspace == "/work/task"


@pytest.mark.parametrize("container_root", ["/workspace/AgentEvolver", "/AgentEvolver", "/custom/repo"])
def test_peer_mount_sources_resolve_in_the_daemon_namespace(monkeypatch, container_root):
    monkeypatch.setenv("AGENTEVOLVER_HOST_ROOT", "/host/repo")
    monkeypatch.setenv("AGENTEVOLVER_CONTAINER_ROOT", container_root)
    assert to_host_path(container_root) == "/host/repo"
    assert to_host_path(container_root + "/output/run") == "/host/repo/output/run"
    assert to_host_path(container_root + "-other/run") == container_root + "-other/run"
    args = DockerSandbox(SandboxConfig(image="test", mounts={
        container_root + "/output/run": "/workspace", container_root + "/agentevolver": "/workspace/.agentevolver/package",
    }))._run_args()
    assert "/host/repo/output/run:/workspace:rw" in args
    assert "/host/repo/agentevolver:/workspace/.agentevolver/package:rw" in args


def test_legacy_base_container_mapping_remains_usable(monkeypatch):
    monkeypatch.setenv("AGENTEVOLVER_HOST_ROOT", "/host/repo")
    monkeypatch.delenv("AGENTEVOLVER_CONTAINER_ROOT", raising=False)
    assert to_host_path("/AgentEvolver/output/run") == "/host/repo/output/run"


def test_session_resources_are_all_writable_and_under_workspace(tmp_path):
    sandbox = ProjectSandbox.create(tmp_path / "session", package_root=tmp_path / "package",
                                    shared_extension_root=tmp_path / "shared")
    mounts = sandbox.mounts()
    assert {item["source"] for item in mounts} == {
        str(getattr(sandbox, name)) for name in
        ("workspace_root", "extension_root", "log_root", "package_root", "shared_extension_root")
    }
    assert all(item["mode"] == "rw" for item in mounts)
    assert all(Path(item["target"]).is_relative_to("/workspace") for item in mounts)


@pytest.mark.asyncio
async def test_opensandbox_passes_writable_volumes_and_command_workdir(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from opensandbox import Sandbox as OSSandbox

    handle = SimpleNamespace(get_info=AsyncMock(return_value=SimpleNamespace(id="")),
                             commands=SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace())))
    create = AsyncMock(return_value=handle)
    monkeypatch.setattr(OSSandbox, "create", create)
    monkeypatch.setattr("agentevolver.sandbox.default.base.ensure_server", AsyncMock())
    monkeypatch.setenv("AGENTEVOLVER_HOST_ROOT", "/host/repo")
    monkeypatch.setenv("AGENTEVOLVER_CONTAINER_ROOT", "/workspace/AgentEvolver")
    sandbox = OpenSandbox(SandboxConfig(domain="localhost:8080", workdir="/work/task", mounts={
        "/workspace/AgentEvolver/agentevolver": "/work/task/.agentevolver/package",
    }))
    await sandbox.start()
    volume, = create.call_args.kwargs["volumes"]
    assert volume.host.path == "/host/repo/agentevolver"
    assert volume.mount_path == "/work/task/.agentevolver/package"
    assert volume.read_only is False
    await sandbox.run_command("pwd")
    assert handle.commands.run.call_args.kwargs["opts"].working_directory == "/work/task"


@pytest.mark.asyncio
async def test_ide_launch_mounts_live_framework_and_writable_library(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from agentevolver.ide.server import IdeManagerServer

    handle = Mock(start=AsyncMock(), code_url=AsyncMock(return_value="http://localhost:1234"))
    factory = Mock(return_value=handle)
    monkeypatch.setattr("agentevolver.sandbox.default.vscode.VscodeSandbox", factory)
    manager = IdeManagerServer()
    monkeypatch.setattr(manager, "_ensure_reaper", lambda: None)
    monkeypatch.setattr(manager, "_wait_ready", AsyncMock(return_value=True))
    await manager.start("mount-check", workspace_root=tmp_path / "workspace")
    config = factory.call_args.args[0]
    assert config.workdir == "/workspace"
    assert all(Path(target).is_relative_to(config.workdir) for target in config.mounts.values())
    assert config.mounts[str(path_manager.package_dir().parent)] == config.env["PYTHONPATH"]
    assert config.mounts[str(path_manager.get(P.EXTENSION))] == config.env["AGENTEVOLVER_EXTENSION_ROOT"]
    assert config.env["HOME"] in config.mounts.values()
