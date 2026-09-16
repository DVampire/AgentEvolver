"""Report local IDE image failures before falling through to a registry pull."""

from unittest.mock import AsyncMock

import pytest

from agentevolver.sandbox.default import vscode
from agentevolver.sandbox.default.base import OpenSandbox
from agentevolver.sandbox.types import SandboxConfig


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM agentevolver/base:latest\n")
    monkeypatch.setattr(vscode, "_DOCKERFILE_DIR", tmp_path)
    monkeypatch.setattr(vscode.shutil, "which", lambda _: "/usr/bin/docker")
    return vscode.VscodeSandbox()


@pytest.mark.asyncio
async def test_missing_base_does_not_try_to_pull_ide(sandbox, monkeypatch):
    docker = AsyncMock(return_value=1)
    runtime_start = AsyncMock()
    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(OpenSandbox, "start", runtime_start)
    with pytest.raises(RuntimeError, match="docker build -f docker/base/Dockerfile"):
        await sandbox.start()
    runtime_start.assert_not_awaited()
    assert not any(call.args[0][0] == "build" for call in docker.await_args_list)


@pytest.mark.asyncio
async def test_failed_build_never_attempts_runtime_pull(sandbox, monkeypatch):
    monkeypatch.setattr(sandbox, "_docker", AsyncMock(side_effect=[1, 0, 7]))
    runtime_start = AsyncMock()
    monkeypatch.setattr(OpenSandbox, "start", runtime_start)
    with pytest.raises(RuntimeError, match="Docker exit 7"):
        await sandbox.start()
    runtime_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_ide_image_needs_no_base_or_build(sandbox, monkeypatch):
    docker = AsyncMock(return_value=0)
    monkeypatch.setattr(sandbox, "_docker", docker)
    await sandbox._ensure_image()
    docker.assert_awaited_once_with(["image", "inspect", vscode._IMAGE], quiet=True)


@pytest.mark.asyncio
async def test_successful_build_starts_runtime(sandbox, monkeypatch):
    docker = AsyncMock(side_effect=[1, 0, 0])
    runtime_start = AsyncMock()
    monkeypatch.setattr(sandbox, "_docker", docker)
    monkeypatch.setattr(OpenSandbox, "start", runtime_start)
    await sandbox.start()
    runtime_start.assert_awaited_once()
    assert docker.await_args.args[0][:3] == ["build", "-t", vscode._IMAGE]


@pytest.mark.asyncio
async def test_custom_image_remains_runtime_managed(monkeypatch):
    sandbox = vscode.VscodeSandbox(SandboxConfig(image="example/custom-ide:1"))
    docker = AsyncMock()
    monkeypatch.setattr(sandbox, "_docker", docker)
    await sandbox._ensure_image()
    docker.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_source_reports_local_setup_error(sandbox, monkeypatch, tmp_path):
    (tmp_path / "Dockerfile").unlink()
    monkeypatch.setattr(sandbox, "_docker", AsyncMock(return_value=1))
    with pytest.raises(RuntimeError, match="build files were not found"):
        await sandbox._ensure_image()
