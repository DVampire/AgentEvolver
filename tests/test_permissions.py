import asyncio
from pathlib import Path
from types import SimpleNamespace

from agentevolver.permission import PermissionMode
from agentevolver.permission.types import check_file_write, validate_command
from agentevolver.tool.default.bash import BashTool
from agentevolver.sandbox import ExecResult, use_sandbox


def test_workspace_path_uses_component_boundary(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    assert check_file_write(str(workspace / "ok.txt"), "x", PermissionMode.WORKSPACE_WRITE, str(workspace)).allowed
    sibling = tmp_path / "workspace-escape" / "bad.txt"
    result = check_file_write(str(sibling), "x", PermissionMode.WORKSPACE_WRITE, str(workspace))
    assert not result.allowed


def test_read_only_does_not_trust_general_purpose_executables() -> None:
    from agentevolver.permission.types import CommandIntent, _classify_intent

    for command in ("python -c pass", "node script.js", "curl https://example.com", "tee output"):
        assert _classify_intent(command) is CommandIntent.UNKNOWN
        assert not validate_command(command, PermissionMode.READ_ONLY).allowed


def test_read_only_blocks_chained_redirect_and_git_write() -> None:
    assert not validate_command("ls > output", PermissionMode.READ_ONLY).allowed
    assert not validate_command("git reset --hard", PermissionMode.READ_ONLY).allowed
    assert not validate_command("ls && rm -rf elsewhere", PermissionMode.READ_ONLY).allowed


def test_restricted_bash_never_falls_back_to_host(tmp_path: Path) -> None:
    tool = BashTool(permission_mode="workspace_write")
    ctx = SimpleNamespace(workspace_root=str(tmp_path), extra={})
    response = asyncio.run(tool(command="pwd", ctx=ctx))
    assert not response.success
    assert "Sandbox blocked host Bash" in response.message


def test_gateway_never_allows_host_bash_even_in_danger_mode(tmp_path: Path) -> None:
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(workspace_root=str(tmp_path), extra={"gateway_session": True})
    response = asyncio.run(tool(command="pwd", ctx=ctx))
    assert not response.success
    assert "Sandbox blocked host Bash" in response.message


def test_restricted_bash_executes_through_bound_sandbox(tmp_path: Path) -> None:
    class FakeSandbox:
        command = None

        async def run_command(self, command: str) -> ExecResult:
            self.command = command
            return ExecResult(success=True, stdout="sandbox", exit_code=0)

    sandbox = FakeSandbox()
    tool = BashTool(permission_mode="workspace_write")
    ctx = SimpleNamespace(workspace_root=str(tmp_path), extra={})
    with use_sandbox(sandbox):
        response = asyncio.run(tool(command="pwd", ctx=ctx))
    assert response.success
    assert response.data["sandboxed"] is True
    assert sandbox.command == "pwd"
    assert response.message == "sandbox"
