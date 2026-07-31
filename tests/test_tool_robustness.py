"""Regression tests for tool-layer robustness fixes.

Three papercuts observed in a real MetaAgent run, each fixed and pinned here:

1. A model tool call that omits a required parameter (e.g. ``done_tool`` without
   ``result``) used to raise a raw ``TypeError`` out of the central dispatch and
   surface as an opaque "Action failed" — now it returns a clean, recoverable
   Response naming the missing parameter.
2. ``bash_tool`` used to mark every non-zero exit code as ``success=False``, so
   ordinary diagnostics (``grep -c`` with no match → exit 1, ``ls missing`` →
   exit 2) were mislabeled as failed actions. A command that runs to completion is
   now a successful observation with the exit code carried in ``data``/message.
4. That same fix reached only the *local* execution path; commands routed into a
   peer sandbox still returned ``success=ExecResult.success``, i.e. non-zero exit
   → failed action. ProgramBench runs entirely through the sandbox path, and the
   asymmetry cost a real run 11 points: after ``compile.sh`` left an untracked
   binary, ``git add <files> && git commit`` exited 1 with "nothing added to
   commit" — the work was *already committed* — and the agent, told its action had
   failed, retried the same commit 11 times.
"""
import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.config import config
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.default.bash import BashTool
from agentevolver.tool.default.done import DoneTool


# --------------------------------------------------------------------------- #
# Fix #2 — missing/invalid arguments become a recoverable tool error
# --------------------------------------------------------------------------- #
def _manager_for(tmp_path, instance):
    manager = ToolContextManager(base_dir=str(tmp_path))

    async def _fake_get_info(name):
        return SimpleNamespace(version="1.0.0", instance=instance)

    manager.get_info = _fake_get_info
    return manager


@pytest.mark.asyncio
async def test_missing_required_arg_returns_clean_error_not_typeerror(tmp_path):
    manager = _manager_for(tmp_path, DoneTool())
    # done_tool requires both `reasoning` and `result`; omit `result`.
    resp = await manager(name="done_tool", input={"reasoning": "all conditions met"})
    assert resp.success is False
    # The message names the offending tool and the parameter the model forgot,
    # rather than leaking "__call__() missing 1 required positional argument".
    assert "done_tool" in resp.message
    assert "result" in resp.message
    assert "positional argument" not in resp.message


@pytest.mark.asyncio
async def test_valid_call_still_dispatches(tmp_path):
    manager = _manager_for(tmp_path, DoneTool())
    resp = await manager(name="done_tool", input={"reasoning": "r", "result": "ok"})
    assert resp.success is True
    assert resp.message == "ok"


@pytest.mark.asyncio
async def test_in_body_errors_are_not_masked_by_bind_check(tmp_path):
    class _Boom(DoneTool):
        async def __call__(self, reasoning: str, result: str, **kwargs):
            raise ValueError("boom inside body")

    manager = _manager_for(tmp_path, _Boom())
    # The bind check must only catch argument-binding errors; a genuine error
    # raised inside the tool body must still propagate untouched.
    with pytest.raises(ValueError, match="boom inside body"):
        await manager(name="done_tool", input={"reasoning": "r", "result": "ok"})


# --------------------------------------------------------------------------- #
# Fix #3 — a bash command that runs is a success, exit code is an observation
# --------------------------------------------------------------------------- #
def test_bash_nonzero_exit_is_successful_observation(tmp_path):
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(extra={})
    # `grep -c` prints "0" and exits 1 when there are no matches — the canonical
    # false-failure case.
    resp = asyncio.run(tool(command="echo needle | grep -c missing", ctx=ctx))
    assert resp.success is True
    assert resp.data["exit_code"] == 1
    assert "Exit code: 1" in resp.message


def test_bash_true_failure_still_visible(tmp_path):
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(extra={})
    resp = asyncio.run(tool(command="ls /no/such/path/here", ctx=ctx))
    # The tool call succeeds (it ran), but the failure is fully legible: the
    # exit code and stderr are in the response for the model to read.
    assert resp.success is True
    assert resp.data["exit_code"] != 0
    assert "Exit code:" in resp.message
    assert "STDERR" in resp.message


def test_bash_empty_command_is_a_tool_error(tmp_path):
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(extra={})
    resp = asyncio.run(tool(command="   ", ctx=ctx))
    assert resp.success is False


# --------------------------------------------------------------------------- #
# Fix #4 — the sandbox path honors the same contract as the local one
# --------------------------------------------------------------------------- #
class _FakeSandbox:
    """Minimal peer sandbox: returns a canned ExecResult for any command."""

    container_workspace = "/workspace"

    def __init__(self, result):
        self._result = result
        self.commands_run = []

    async def run_command(self, command, **kwargs):
        self.commands_run.append(command)
        return self._result


def _sandbox_exec(result, tmp_path, command="git commit -m x"):
    from agentevolver.sandbox.types import ExecResult  # noqa: PLC0415

    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    sandbox = _FakeSandbox(result if isinstance(result, ExecResult) else ExecResult(**result))
    ctx = SimpleNamespace(extra={"sandbox": sandbox})
    return asyncio.run(tool(command=command, ctx=ctx))


def test_sandboxed_nothing_to_commit_is_an_observation_not_a_failure(tmp_path):
    """The exact shape that sent a ProgramBench run into an 11-retry loop."""
    resp = _sandbox_exec(dict(
        success=False,          # ExecResult's own rule: non-zero exit => not success
        stdout=('On branch master\nUntracked files:\n\texecutable\n'
                'nothing added to commit but untracked files present'),
        error="CommandExecError: 1",
        exit_code=1,
    ), tmp_path)
    assert resp.success is True, "a command that ran is an observation, not a tool failure"
    assert resp.data["exit_code"] == 1
    assert resp.data["sandboxed"] is True
    # The agent must still be able to see what happened.
    assert "nothing added to commit" in resp.message


def test_sandboxed_zero_exit_is_success(tmp_path):
    resp = _sandbox_exec(dict(success=True, stdout="ok", exit_code=0), tmp_path)
    assert resp.success is True
    assert resp.data["exit_code"] == 0


def test_sandboxed_command_that_never_ran_is_a_tool_failure(tmp_path):
    """No exit code means the shell never reached a verdict — a real failure."""
    resp = _sandbox_exec(dict(
        success=False, error="command failed: container is gone", exit_code=None,
    ), tmp_path)
    assert resp.success is False
    assert "container is gone" in resp.message


def test_local_and_sandboxed_paths_agree_on_nonzero_exit(tmp_path):
    """The two branches must not drift apart again."""
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    local = asyncio.run(tool(command="exit 3", ctx=SimpleNamespace(extra={})))
    sandboxed = _sandbox_exec(
        dict(success=False, stdout="", error="CommandExecError: 3", exit_code=3), tmp_path
    )
    assert local.success == sandboxed.success is True
    assert local.data["exit_code"] == sandboxed.data["exit_code"] == 3


# --------------------------------------------------------------------------- #
# Fix #5 — search tools follow the sandbox too
# --------------------------------------------------------------------------- #
# grep_search and glob_search walked the local filesystem unconditionally. With a
# peer cleanroom bound that is the base container, where the task's /workspace does
# not exist — so both would answer "no matches" about the wrong machine, which reads
# as a fact about the code rather than about the tool.
class _SearchSandbox:
    container_workspace = "/workspace"

    def __init__(self, stdout, exit_code=0):
        from agentevolver.sandbox.types import ExecResult  # noqa: PLC0415
        self._result = ExecResult(success=exit_code == 0, stdout=stdout, exit_code=exit_code)
        self.commands_run = []

    async def run_command(self, command, **kwargs):
        self.commands_run.append(command)
        return self._result


def test_grep_search_routes_into_the_sandbox(tmp_path):
    from agentevolver.tool.default.grep_search import GrepSearchTool

    config.workspace_root = str(tmp_path)
    sandbox = _SearchSandbox("/workspace/cmatrix.c:42:    int rows = 0;\n")
    tool = GrepSearchTool(permission_mode="danger_full_access")
    resp = asyncio.run(tool(
        pattern="int rows", root="/workspace",
        ctx=SimpleNamespace(extra={"sandbox": sandbox}),
    ))
    assert resp.success is True
    assert resp.data["sandboxed"] is True
    assert resp.data["results"] == [
        {"file": "/workspace/cmatrix.c", "line": 42, "text": "    int rows = 0;"}
    ]
    # It must have asked the container, not the host.
    assert sandbox.commands_run and "grep" in sandbox.commands_run[0]
    assert "/workspace" in sandbox.commands_run[0]


def test_grep_search_no_matches_is_not_an_error(tmp_path):
    """grep exits 1 on no matches; that is an answer."""
    from agentevolver.tool.default.grep_search import GrepSearchTool

    config.workspace_root = str(tmp_path)
    tool = GrepSearchTool(permission_mode="danger_full_access")
    resp = asyncio.run(tool(
        pattern="nothing", root="/workspace",
        ctx=SimpleNamespace(extra={"sandbox": _SearchSandbox("", exit_code=1)}),
    ))
    assert resp.success is True
    assert resp.data["results"] == []
    assert "No matches" in resp.message


def test_glob_search_routes_into_the_sandbox_and_matches_like_the_local_branch(tmp_path):
    from agentevolver.tool.default.glob_search import GlobSearchTool

    config.workspace_root = str(tmp_path)
    listing = "/workspace/cmatrix.c\n/workspace/compile.sh\n/workspace/src/util.c\n"
    sandbox = _SearchSandbox(listing)
    tool = GlobSearchTool(permission_mode="danger_full_access")
    resp = asyncio.run(tool(
        pattern="*.c", root="/workspace",
        ctx=SimpleNamespace(extra={"sandbox": sandbox}),
    ))
    assert resp.success is True
    assert resp.data["sandboxed"] is True
    # Both the nested path and the top-level file match, as with fnmatch locally.
    assert resp.data["matches"] == ["/workspace/cmatrix.c", "/workspace/src/util.c"]
    assert "find" in sandbox.commands_run[0]


def test_search_tools_still_work_locally_without_a_sandbox(tmp_path):
    from agentevolver.tool.default.glob_search import GlobSearchTool
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "a.c").write_text("int rows = 0;\n")
    config.workspace_root = str(tmp_path)
    ctx = SimpleNamespace(extra={})

    g = asyncio.run(GlobSearchTool(permission_mode="danger_full_access")(
        pattern="*.c", root=str(tmp_path), ctx=ctx))
    assert g.success is True and len(g.data["matches"]) == 1
    assert "sandboxed" not in (g.data or {})

    r = asyncio.run(GrepSearchTool(permission_mode="danger_full_access")(
        pattern="int rows", root=str(tmp_path), ctx=ctx))
    assert r.success is True and len(r.data["results"]) == 1


# --------------------------------------------------------------------------- #
# Fix #6 — code_interpreter can skip the kernel and run inside the sandbox
# --------------------------------------------------------------------------- #
# The kernel starts in the base environment. On ProgramBench the task fixture is in
# a peer cleanroom, so a script asking for /workspace/cmatrix.c got
# FileNotFoundError — and that script was rewriting print_help()/print_version(),
# the fix for the benchmark's largest failure class.
def test_code_interpreter_defaults_to_the_kernel():
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    assert CodeInterpreterTool().use_kernel is True


def test_one_shot_mode_drops_the_persistence_promise():
    """The instruction is what the agent plans against; a stale promise misleads it."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    kernel = CodeInterpreterTool()
    one_shot = CodeInterpreterTool(use_kernel=False)
    assert "State persists across calls" in kernel.instruction
    assert "State persists across calls" not in one_shot.instruction
    assert "NOTHING carries over" in one_shot.instruction


def test_one_shot_runs_inside_the_sandbox(tmp_path):
    from agentevolver.sandbox.types import ExecResult
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    class _Sandbox:
        container_workspace = "/workspace"

        def __init__(self):
            self.written = {}
            self.commands_run = []

        async def write_file(self, path, data, **kwargs):
            self.written[path] = data

        async def run_command(self, command, **kwargs):
            self.commands_run.append(command)
            return ExecResult(success=True, stdout="4", exit_code=0)

    config.workspace_root = str(tmp_path)
    sandbox = _Sandbox()
    tool = CodeInterpreterTool(use_kernel=False, permission_mode="danger_full_access")
    resp = asyncio.run(tool(code="print(2 + 2)", language="python",
                            ctx=SimpleNamespace(extra={"sandbox": sandbox})))
    assert resp.success is True
    assert resp.data["sandboxed"] is True and resp.data["use_kernel"] is False
    # Written into the container's workspace, then run there.
    assert any(p.startswith("/workspace/") for p in sandbox.written)
    assert "python3" in sandbox.commands_run[0]
    assert "/workspace" in sandbox.commands_run[0]


def test_one_shot_nonzero_exit_is_an_observation(tmp_path):
    """A script that runs and fails has told the agent something."""
    from agentevolver.sandbox.types import ExecResult
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    class _Sandbox:
        container_workspace = "/workspace"

        async def write_file(self, path, data, **kwargs):
            return None

        async def run_command(self, command, **kwargs):
            return ExecResult(success=False, stdout="Traceback ...", exit_code=1)

    config.workspace_root = str(tmp_path)
    tool = CodeInterpreterTool(use_kernel=False, permission_mode="danger_full_access")
    resp = asyncio.run(tool(code="raise SystemExit(1)", language="python",
                            ctx=SimpleNamespace(extra={"sandbox": _Sandbox()})))
    assert resp.success is True
    assert resp.data["exit_code"] == 1
    assert "Traceback" in resp.message


def test_one_shot_rejects_a_language_it_cannot_run(tmp_path):
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    config.workspace_root = str(tmp_path)
    tool = CodeInterpreterTool(use_kernel=False, permission_mode="danger_full_access")
    resp = asyncio.run(tool(code="x", language="brainfuck", ctx=SimpleNamespace(extra={})))
    assert resp.success is False
    assert "Unsupported language" in resp.message


def test_one_shot_runs_locally_when_no_sandbox_is_bound(tmp_path):
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    config.workspace_root = str(tmp_path)
    tool = CodeInterpreterTool(use_kernel=False, permission_mode="danger_full_access")
    resp = asyncio.run(tool(code="print(2 + 2)", language="python",
                            ctx=SimpleNamespace(extra={})))
    assert resp.success is True
    assert "4" in resp.message
    assert resp.data["use_kernel"] is False


# --------------------------------------------------------------------------- #
# Fix #7 — a sandboxed timeout says so
# --------------------------------------------------------------------------- #
# The local branch has reported "Command timed out after Ns" for a long time. The
# sandbox branch let asyncio.TimeoutError reach the generic handler, whose str() is
# empty, so the agent read the literal "Error executing command: " — indistinguishable
# from a crash. Seen on ProgramBench: `./executable -z` on a reconstruction that fell
# into its TUI loop blocked for the full 600s and the agent learned nothing from it.
def test_sandboxed_timeout_names_the_cause_and_suggests_the_fix(tmp_path):
    class _HangingSandbox:
        container_workspace = "/workspace"

        async def run_command(self, command, **kwargs):
            await asyncio.sleep(60)  # never finishes within the tool's timeout

    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access", timeout=1)
    resp = asyncio.run(tool(command="./executable -z",
                            ctx=SimpleNamespace(extra={"sandbox": _HangingSandbox()})))
    assert resp.success is False
    assert resp.data["timed_out"] is True
    assert "timed out after 1 seconds" in resp.message
    # The agent must be able to act on it next time.
    assert "timeout 2" in resp.message
    assert "124" in resp.message
    assert "./executable -z" in resp.message


def test_local_and_sandboxed_timeouts_both_report_a_timeout(tmp_path):
    """The two branches must not drift apart on this either."""
    class _HangingSandbox:
        container_workspace = "/workspace"

        async def run_command(self, command, **kwargs):
            await asyncio.sleep(60)

    config.workspace_root = str(tmp_path)
    local = asyncio.run(BashTool(permission_mode="danger_full_access", timeout=1)(
        command="sleep 30", ctx=SimpleNamespace(extra={})))
    sandboxed = asyncio.run(BashTool(permission_mode="danger_full_access", timeout=1)(
        command="sleep 30", ctx=SimpleNamespace(extra={"sandbox": _HangingSandbox()})))
    for resp in (local, sandboxed):
        assert resp.success is False
        assert "timed out" in resp.message
