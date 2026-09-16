"""Mounted source and extension libraries are writable; explicit read-only stays read-only."""

import pytest

from agentevolver.paths import path_manager
from agentevolver.permission.types import PermissionMode, check_file_write, validate_command
from agentevolver.sandbox.project import check_session_path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTEVOLVER_EXTENSION_ROOT", str(tmp_path / "shared"))
    monkeypatch.setattr(path_manager, "package_dir", lambda: tmp_path / "package")
    path_manager.bind_session("local", "writable-resources")
    roots = path_manager.session_roots()
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    return roots


@pytest.mark.parametrize("resource", ["workspace", "extension", "log", "package", "shared_extension", "plan"])
@pytest.mark.parametrize("mode", [PermissionMode.WORKSPACE_WRITE, PermissionMode.DANGER_FULL_ACCESS])
def test_file_and_shell_checks_agree_on_writable_resources(roots, resource, mode):
    path = str(roots[resource] / "probe.py")
    assert check_session_path(path=path, write=True) is None
    assert check_file_write(path, "updated", mode, str(roots["workspace"])).allowed
    assert validate_command(f"echo updated > {path}", mode).allowed


@pytest.mark.parametrize("resource", ["workspace", "extension", "log", "package", "shared_extension", "plan"])
def test_explicit_read_only_mode_still_blocks_writes(roots, resource):
    path = str(roots[resource] / "probe.py")
    assert not check_file_write(path, "updated", PermissionMode.READ_ONLY).allowed
    assert not validate_command(f"echo updated > {path}", PermissionMode.READ_ONLY).allowed


def test_writable_mounts_do_not_grant_other_host_paths(roots, tmp_path):
    path = str(tmp_path / "outside.py")
    assert check_session_path(path=path, write=True)
    assert not check_file_write(path, "updated", PermissionMode.WORKSPACE_WRITE).allowed


def test_scoped_worker_keeps_its_workspace_fence(roots, tmp_path):
    isolated = tmp_path / "isolated"
    assert not check_file_write(str(roots["workspace"] / "parent.py"), "x",
                                PermissionMode.WORKSPACE_WRITE, str(isolated)).allowed
    assert check_file_write(str(roots["shared_extension"] / "tool.py"), "x",
                            PermissionMode.WORKSPACE_WRITE, str(isolated)).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["package", "shared_extension"])
async def test_file_tool_can_really_edit_source_and_shared_library(roots, resource):
    from agentevolver.tool.default.workspace.write_file import WriteFileTool

    path = roots[resource] / "probe.py"
    response = await WriteFileTool()(path=str(path), content="updated\n")
    assert response.success, response.message
    assert path.read_text() == "updated\n"
