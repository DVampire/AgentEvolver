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

    response = asyncio.run(tool(command="echo hello-sandbox", ctx=ctx))

    assert response.success
    assert "hello-sandbox" in response.message
    assert "sandboxed" not in (response.data or {})
