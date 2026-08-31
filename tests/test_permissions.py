"""What a permission mode refuses, and the near-misses it must not let through.

A permission check is only worth its cost at the edges. The middle of the range is easy —
nobody ships a `read_only` mode that allows `rm -rf`. What decides whether the barrier is
real is the case that looks allowed: a path that starts with the workspace but is not
inside it, a command whose *name* is harmless while its effect is not, a write hidden
behind a redirect or a `&&`.

Every case here is one of those. A mode that passes them by accident — because the check
compared prefixes, or trusted an interpreter's name — is a mode that reports a barrier it
does not have, which is worse than no barrier: the runs it guards get treated as
comparable to guarded ones.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agentevolver.config import config
from agentevolver.permission import PermissionMode
from agentevolver.permission.types import check_file_write, validate_command
from agentevolver.tool.default.bash import BashTool


# --------------------------------------------------------------------------- #
# Where the workspace ends
# --------------------------------------------------------------------------- #
def test_a_sibling_sharing_the_workspace_prefix_is_still_outside_it(tmp_path: Path) -> None:
    """`/work` and `/workspace-escape` share a prefix and share nothing else.

    A containment check written as a string prefix admits every sibling whose name starts
    with the workspace's — and those names are attacker-chosen in exactly the situation
    the check exists for. Containment has to be decided on path components.
    """
    workspace = tmp_path / "work"
    workspace.mkdir()

    inside = check_file_write(str(workspace / "ok.txt"), "x",
                              PermissionMode.WORKSPACE_WRITE, str(workspace))
    assert inside.allowed

    # Not a subdirectory: it merely begins with the same characters.
    sibling = tmp_path / "workspace-escape" / "bad.txt"
    outside = check_file_write(str(sibling), "x",
                               PermissionMode.WORKSPACE_WRITE, str(workspace))
    assert not outside.allowed


# --------------------------------------------------------------------------- #
# What read-only refuses
# --------------------------------------------------------------------------- #
def test_a_general_purpose_interpreter_is_never_assumed_read_only() -> None:
    """`python`, `node`, `curl`, `tee` — the name says nothing about the effect.

    An allowlist keyed on command names has to treat these as unknown, because each can
    write anything its argument tells it to. Classifying them by reputation is how a
    read-only mode ends up writing files: the check was right about `python` and wrong
    about `python -c 'open("x","w")'`, and it cannot tell them apart.
    """
    from agentevolver.permission.types import CommandIntent, _classify_intent

    for command in ("python -c pass", "node script.js",
                    "curl https://example.com", "tee output"):
        assert _classify_intent(command) is CommandIntent.UNKNOWN, command
        assert not validate_command(command, PermissionMode.READ_ONLY).allowed, command


def test_a_write_hidden_behind_shell_syntax_is_still_a_write() -> None:
    """The shell composes; a check that reads only the first word does not.

    `ls` is a read. `ls > output` is a write, and `ls && rm -rf elsewhere` is a delete —
    the leading command is identical in all three. Anything that stops at the first token
    approves the whole line on the strength of its safest part.
    """
    assert not validate_command("ls > output", PermissionMode.READ_ONLY).allowed
    assert not validate_command("git reset --hard", PermissionMode.READ_ONLY).allowed
    assert not validate_command("ls && rm -rf elsewhere", PermissionMode.READ_ONLY).allowed


def test_destructive_commands_require_approval_even_with_full_access() -> None:
    result = validate_command("rm -rf build-cache", PermissionMode.DANGER_FULL_ACCESS)
    assert result.allowed
    assert result.requires_approval
    assert "irreversible" in (result.warning or "")


@pytest.mark.parametrize("command", [
    "echo ready && rm -rf build-cache",
    "sudo rm -rf build-cache",
    "VALUE=1 rm -rf build-cache",
])
def test_nested_destructive_commands_still_require_approval(command: str) -> None:
    assert validate_command(
        command, PermissionMode.DANGER_FULL_ACCESS,
    ).requires_approval


def test_unknown_permission_entities_fail_closed() -> None:
    from agentevolver.permission.context import PermissionContextManager
    from agentevolver.permission.types import Operation, PermissionRequest

    result = PermissionContextManager().check(
        "not-registered",
        PermissionRequest(op=Operation.READ, target="README.md"),
    )
    assert not result.allowed
    assert "not registered" in (result.reason or "")


def test_direct_declared_workspace_mode_uses_the_bound_session_root(tmp_path) -> None:
    from agentevolver.paths import P, path_manager
    from agentevolver.permission import permission_manager
    from agentevolver.permission.types import Operation, PermissionRequest

    owner, session = path_manager._owner, path_manager._session_id
    overrides = dict(path_manager._overrides)
    try:
        path_manager.bind_session("permission-test", "session")
        path_manager.override(P.SESSION_WORKSPACE, tmp_path / "workspace")
        outside = tmp_path / "outside.txt"
        result = permission_manager.check_declared(
            "direct-tool",
            PermissionRequest(op=Operation.WRITE, target=str(outside), content="x"),
            mode=PermissionMode.WORKSPACE_WRITE,
        )
    finally:
        path_manager._owner, path_manager._session_id = owner, session
        path_manager._overrides = overrides

    assert not result.allowed
    assert "outside" in (result.reason or "")


# --------------------------------------------------------------------------- #
# The unrestricted mode still has to work
# --------------------------------------------------------------------------- #
def test_full_access_runs_the_command_here_rather_than_reaching_for_a_sandbox(tmp_path: Path) -> None:
    """The agent already runs inside the project container, so "here" is the sandbox.

    There is no separate box to reach into any more. The assertion on `sandboxed` is the
    point: if a response starts carrying that flag again, execution has been re-routed
    somewhere this test is no longer describing, and the mode's meaning has changed
    without its name changing.
    """
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(extra={})
    from agentevolver.permission import permission_manager

    permission_manager.register(tool.name, PermissionMode.DANGER_FULL_ACCESS)
    try:
        response = asyncio.run(tool(command="echo hello-sandbox", ctx=ctx))
    finally:
        permission_manager.unregister(tool.name)

    assert response.success
    assert "hello-sandbox" in response.message
    assert "sandboxed" not in (response.data or {})


# --------------------------------------------------------------------------- #
# A caller that says nothing about the workspace gets the fence, not none
# --------------------------------------------------------------------------- #
def test_an_omitted_workspace_falls_back_to_the_bound_session():
    """`workspace=""` used to mean *no fence*, silently.

    `check_file_write` skipped containment entirely when it was empty, so a caller that
    forgot the argument was unbounded and nothing said so. Every caller in the tree does
    pass it — from `config.workspace_root` — which is what made the default look safe
    while never being exercised. The next tool to call this and forget would have had no
    boundary at all.

    It now asks `path_manager` instead, which is the same source `check_session_path`
    reads, so the two boundaries answer from one place.
    """
    from agentevolver.paths import path_manager
    from agentevolver.permission.types import PermissionMode, check_file_write

    path_manager.bind_session("local", "fence_fallback")
    try:
        workspace = str(path_manager.session_roots()["workspace"])
        assert check_file_write(f"{workspace}/inside.txt", "x",
                                PermissionMode.WORKSPACE_WRITE).allowed
        assert not check_file_write("/etc/hosts", "x",
                                    PermissionMode.WORKSPACE_WRITE).allowed, (
            "an omitted workspace left the write unfenced")
    finally:
        path_manager.unbind_session()


def test_with_no_bound_session_the_config_still_fences():
    """The fallback is `config.workspace_root`, and dropping it would *weaken* the boundary.

    Before this, nine call sites each passed `config.workspace_root`, so a run with no
    bound session — an agent constructed directly, a script — was still fenced by it.
    Resolving only from the bound session looks tidier and quietly removes that fence,
    which is the opposite of the point.

    The order is: the caller's argument, then the bound run, then the config. Only when
    none of the three answers is there no fence, and then there is genuinely nothing to
    be outside of.
    """
    from agentevolver.config import config
    from agentevolver.paths import path_manager
    from agentevolver.permission.types import PermissionMode, check_file_write

    path_manager.unbind_session()
    workspace = str(getattr(config, "workspace_root", "") or "")
    assert workspace, "this test needs a configured workspace to be about anything"

    assert check_file_write(f"{workspace}/inside.txt", "x",
                            PermissionMode.WORKSPACE_WRITE).allowed
    assert not check_file_write("/etc/hosts", "x",
                                PermissionMode.WORKSPACE_WRITE).allowed, (
        "with no session bound the config workspace is the only fence, and it is gone")


def test_the_bound_run_outranks_the_config():
    """They disagree, and the bound run is the one that is current.

    `bind_session` and `override` both move the bound run's workspace and neither touches
    `config.workspace_root`. A container run overrides its workspace to the mount point;
    reading the config there gives the host path, which is what made the fence depend on
    which of the two a caller happened to consult.
    """
    from agentevolver.paths import P, path_manager
    from agentevolver.permission.types import PermissionMode, check_file_write

    path_manager.bind_session("local", "outranks")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")
        assert check_file_write("/workspace/x.c", "x",
                                PermissionMode.WORKSPACE_WRITE).allowed, (
            "the config's stale workspace won over the bound run's")
    finally:
        path_manager.unbind_session()


def test_a_container_mount_is_fenced_by_both_boundaries():
    """The two checks must agree, and they read different sources.

    `check_session_path` reads `path_manager.session_roots()`; the permission fence reads
    whatever its caller passed, which is `config.workspace_root`. A run inside a container
    sets both to the mount point — and the first ignored its override, so
    `write_file_tool` on `/workspace/cmatrix.c` was refused by the sandbox while the
    permission fence would have allowed it. Nine refusals on that one file in a single
    ProgramBench instance.

    Both are asserted here so a future change to either is caught by the same test rather
    than by a benchmark run.
    """
    from agentevolver.paths import P, path_manager
    from agentevolver.permission.types import PermissionMode, check_file_write
    from agentevolver.sandbox.project import check_session_path

    path_manager.bind_session("local", "both_fences")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")
        assert check_session_path(path="/workspace/cmatrix.c", write=True) is None, (
            "the sandbox boundary ignores the container mount override")
        assert check_file_write("/workspace/cmatrix.c", "int main(){}",
                                PermissionMode.WORKSPACE_WRITE).allowed, (
            "the permission fence ignores the container mount override")
        for escape in ("/etc/passwd", "/workspace/../etc/passwd"):
            assert check_session_path(path=escape, write=True), f"sandbox allows {escape}"
            assert not check_file_write(escape, "x",
                                        PermissionMode.WORKSPACE_WRITE).allowed, (
                f"permission fence allows {escape}")
    finally:
        path_manager.unbind_session()
