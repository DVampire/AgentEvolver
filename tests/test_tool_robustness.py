"""A tool result must describe what happened, at a size and a shape the agent can act on.

Every case here began as a run that went wrong while every component reported success.
`git commit` exited 1 with "nothing added to commit" — which means the work was *already*
committed — and, labelled a failed action, sent the agent into eleven retries of the same
commit, spending about 40 of its 65 commands. A `strings` call against a 31KB binary
returned 14,419,441 characters, which went into memory and made every later prompt about
4.3M tokens against a 1,048,576 limit, until the run died of consecutive 400s naming
neither the command nor the size. A model call that left out one argument arrived as
"Action failed" with a raw TypeError inside it. A screen-drawing program run without a
terminal refused to start — and so did the reconstruction being compared against it, so
the two agreed and the comparison said nothing.

One idea runs through all of them: this layer decides what the agent believes about what
it just did. A command that ran and returned a verdict is an observation and its exit code
is data; only being unable to run it at all is a failure. Output is bounded once, in the
dispatch funnel, and says what it dropped so the next command can be narrower. Terminal
bytes are rendered into the screen they describe rather than passed on as wire protocol.
None of these raise when they are wrong. The agent just acts on a false description.
"""

import asyncio
import inspect
import os
from types import SimpleNamespace

import pytest

from agentevolver.config import config
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.default.bash import BashTool
from agentevolver.tool.default.done import DoneTool
from agentevolver.utils.terminal import render_terminal


# --------------------------------------------------------------------------- #
# A malformed tool call is answered, not raised
# --------------------------------------------------------------------------- #
def test_every_builtin_tool_accepts_runtime_context_through_kwargs():
    """Every tool receives manager-owned runtime data outside its model schema.

    The manager always injects ``ctx=``. Requiring one implementation to know that
    concrete keyword breaks the call before its body runs, while declaring ``ctx`` as
    a normal parameter risks exposing framework state in the provider schema. Built-in
    tools therefore share one boundary: explicit model arguments plus ``**kwargs``.
    """
    import agentevolver.tool.default  # noqa: F401 — registers built-in tools
    from agentevolver.registry import TOOL

    offenders = []
    for name, cls in sorted(TOOL.module_dict.items()):
        parameters = inspect.signature(cls.__call__).parameters.values()
        if not any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters):
            offenders.append(f"{name}: {inspect.signature(cls.__call__)}")

    assert not offenders, "tools reject runtime ctx:\n  " + "\n  ".join(offenders)


@pytest.mark.asyncio
async def test_a_tool_without_runtime_kwargs_is_rejected_when_registered(tmp_path):
    """An incompatible evolved tool fails before an agent can spend a call on it."""
    from agentevolver.tool.types import Tool

    class _MissingRuntimeChannel(Tool):
        name: str = "missing_runtime_channel_tool"
        description: str = "Bad contract fixture"

        async def __call__(self, value: str):
            return value

    manager = ToolContextManager(base_dir=str(tmp_path))
    with pytest.raises(TypeError, match=r"must accept \*\*kwargs"):
        await manager.register(_MissingRuntimeChannel)


def _manager_for(tmp_path, instance):
    manager = ToolContextManager(base_dir=str(tmp_path))

    async def _fake_get_info(name):
        return SimpleNamespace(version="1.0.0", instance=instance)

    manager.get_info = _fake_get_info
    return manager


@pytest.mark.asyncio
async def test_a_call_missing_a_parameter_comes_back_as_a_correctable_answer(tmp_path):
    """A model that forgot an argument can supply it — if it is told which one.

    Binding straight to the tool's signature raises a TypeError out of the central
    dispatch, and the agent receives "Action failed:" wrapped around
    "__call__() missing 1 required positional argument". That names an internal frame
    rather than the mistake, so the next attempt is a guess at what went wrong rather
    than the same call with one more field.
    """
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
async def test_a_well_formed_call_still_reaches_the_tool(tmp_path):
    """The control. A binding check that rejected everything would satisfy the test above
    and quietly disable every tool in the system."""
    manager = _manager_for(tmp_path, DoneTool())
    resp = await manager(name="done_tool", input={"reasoning": "r", "result": "ok"})
    assert resp.success is True
    assert resp.message == "ok"


@pytest.mark.asyncio
async def test_a_failure_inside_the_tool_is_not_reported_as_a_bad_call(tmp_path):
    """The bind check must catch argument errors and nothing else.

    A tool body that raises TypeError itself — and plenty do, on bad input — would
    otherwise be described to the model as "invalid arguments", which is advice to change
    a call that was already correct. The execution boundary must preserve its real type
    and message under a distinct code so Trace records the actual failure.
    """

    class _Boom(DoneTool):
        async def __call__(self, reasoning: str, result: str, **kwargs):
            raise ValueError("boom inside body")

    manager = _manager_for(tmp_path, _Boom())
    # The bind check catches only binding errors. The authoritative execution boundary
    # then normalizes a body exception with a different stable code, so direct callers,
    # Agent, Workflow, and Code Mode all observe the same failure contract.
    response = await manager(
        name="done_tool",
        input={"reasoning": "r", "result": "ok"},
    )
    assert response.success is False
    assert "boom inside body" in response.message
    assert "Invalid arguments" not in response.message
    assert response.extra["execution"]["error_code"] == "execution_error"


# --------------------------------------------------------------------------- #
# A command that ran is an observation; its exit code is part of what was observed
# --------------------------------------------------------------------------- #
def test_a_nonzero_exit_is_an_observation_not_a_tool_failure(tmp_path):
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(extra={})
    # `grep -c` prints "0" and exits 1 when there are no matches — the canonical
    # false-failure case.
    resp = asyncio.run(tool(command="echo needle | grep -c missing", ctx=ctx))
    assert resp.success is True
    assert resp.data["exit_code"] == 1
    assert "Exit code: 1" in resp.message


def test_a_command_that_really_failed_is_still_fully_legible(tmp_path):
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


def test_an_empty_command_is_a_tool_error(tmp_path):
    """The other side of the line. Nothing ran, so there is no observation to report —
    and a shell handed whitespace exits 0, which as a "successful observation" would tell
    the agent its command worked."""
    config.workspace_root = str(tmp_path)
    tool = BashTool(permission_mode="danger_full_access")
    ctx = SimpleNamespace(extra={})
    resp = asyncio.run(tool(command="   ", ctx=ctx))
    assert resp.success is False


# --------------------------------------------------------------------------- #
# One execution path, and the contracts the second one used to break
# --------------------------------------------------------------------------- #
# These tools each used to carry a second, sandbox-routing branch that decided at call
# time whether to act on the local filesystem or inside a bound peer container. Each
# branch drifted from the other in its own way: bash called a non-zero exit a tool
# failure, grep_search returned "no matches" about the wrong machine, list_dir ignored
# its own ignore list, code_interpreter's kernel could not see the files. The agent now
# runs in the container its tools run in, so there is one path and nothing to keep in
# agreement — but the contracts those fixes established still hold, and are what these
# assert.


def test_nothing_to_commit_is_an_observation_not_a_failure(tmp_path):
    """The exact shape that sent a run into an 11-retry loop: `git commit` exits 1 with
    "nothing added to commit" — which means the work was *already committed*. Framed as
    a hard failure, the agent read it as broken staging and retried variants of the same
    commit 11 times, burning ~40 of its 65 commands."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="echo 'nothing added to commit but untracked files present'; exit 1",
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert resp.success is True
    assert resp.data["exit_code"] == 1
    assert "nothing added to commit" in resp.message


def test_a_command_that_cannot_run_at_all_is_a_tool_failure(tmp_path):
    """The line between the two: a command that ran and returned a verdict is an
    observation; being unable to run one is a failure."""
    config.workspace_root = str(tmp_path / "does-not-exist")
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="echo hi", ctx=SimpleNamespace(extra={})
        )
    )
    assert resp.success is False


def test_search_tools_report_no_matches_as_an_answer(tmp_path):
    """grep exiting 1 for "found nothing" is a result, not an error."""
    from agentevolver.tool.default.glob_search import GlobSearchTool
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "a.c").write_text("int rows = 0;\n")
    config.workspace_root = str(tmp_path)
    ctx = SimpleNamespace(extra={})

    r = asyncio.run(
        GrepSearchTool(permission_mode="danger_full_access")(
            pattern="nothing matches this", root=str(tmp_path), ctx=ctx
        )
    )
    assert r.success is True
    assert r.data["results"] == []
    assert "No matches" in r.message

    g = asyncio.run(
        GlobSearchTool(permission_mode="danger_full_access")(
            pattern="*.nope", root=str(tmp_path), ctx=ctx
        )
    )
    assert g.success is True
    assert g.data["matches"] == []


def test_search_tools_find_what_is_there(tmp_path):
    """The control for the pair above: a search that reported "no matches" for everything
    would satisfy them and leave the agent unable to find a file it just wrote."""
    from agentevolver.tool.default.glob_search import GlobSearchTool
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "a.c").write_text("int rows = 0;\n")
    config.workspace_root = str(tmp_path)
    ctx = SimpleNamespace(extra={})

    g = asyncio.run(
        GlobSearchTool(permission_mode="danger_full_access")(
            pattern="*.c", root=str(tmp_path), ctx=ctx
        )
    )
    assert g.success is True and len(g.data["matches"]) == 1

    r = asyncio.run(
        GrepSearchTool(permission_mode="danger_full_access")(
            pattern="int rows", root=str(tmp_path), ctx=ctx
        )
    )
    assert r.success is True and len(r.data["results"]) == 1


def test_list_dir_prunes_noise_directories_by_default(tmp_path):
    """Listing a workspace with a git repo in it once returned 41 lines of .git
    plumbing wrapped around 9 lines of actual content."""
    from agentevolver.tool.default.list_dir import ListDirTool

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG").write_text("x")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "README.md").write_text("readme")
    config.workspace_root = str(tmp_path)

    resp = asyncio.run(ListDirTool()(path=str(tmp_path), ctx=SimpleNamespace(extra={})))
    assert resp.success is True
    assert "README.md" in resp.message
    assert ".git" not in resp.message and "__pycache__" not in resp.message


def test_list_dir_honours_an_explicit_ignore(tmp_path):
    """A caller's `ignore` argument must not be silently dropped."""
    from agentevolver.tool.default.list_dir import ListDirTool

    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "build.o").write_text("x")
    (tmp_path / "keep.c").write_text("int main(){}")
    config.workspace_root = str(tmp_path)

    resp = asyncio.run(
        ListDirTool()(path=str(tmp_path), ignore=["target"], ctx=SimpleNamespace(extra={}))
    )
    assert "keep.c" in resp.message
    assert "target" not in resp.message


# --------------------------------------------------------------------------- #
# code_interpreter: kernel by default, one-shot when the kernel is the wrong lens
# --------------------------------------------------------------------------- #
def test_code_interpreter_defaults_to_the_kernel():
    """State across calls is the point of an interpreter — a loaded dataframe survives to
    the next question. One-shot is the exception a caller asks for, so the default has to
    stay the kernel or every multi-step analysis starts from nothing."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    assert CodeInterpreterTool().use_kernel is True


def test_one_shot_mode_drops_the_persistence_promise():
    """The guidance is what the agent plans against; a stale promise misleads it.

    Read off `guidance` rather than `instruction`: the two modes swap the text a model
    plans against, and that text moved into its own field when a tool's documentation
    was split from one blob into `guidance` + `examples`. A test still reading the blob
    passes vacuously — it did, against an empty string.
    """
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    kernel = CodeInterpreterTool()
    one_shot = CodeInterpreterTool(use_kernel=False)
    assert "State persists across calls" in kernel.guidance
    assert "State persists across calls" not in one_shot.guidance
    assert "NOTHING carries over" in one_shot.guidance


def test_one_shot_sees_the_filesystem_as_it_is_now(tmp_path):
    """The reason one-shot exists: a kernel held open from before a file appeared
    answered FileNotFoundError to the very script that would have used it."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    (tmp_path / "target.c").write_text("int main(){}\n")
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        CodeInterpreterTool(use_kernel=False)(
            code="import pathlib; print('found:', pathlib.Path('target.c').exists())",
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert resp.success is True
    assert "found: True" in resp.message


def test_one_shot_nonzero_exit_is_an_observation(tmp_path):
    """A script that ran and failed has told the agent something; calling that a tool
    malfunction hides the output it needs."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        CodeInterpreterTool(use_kernel=False)(
            code="import sys; print('partial output'); sys.exit(3)",
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert resp.success is True
    assert "partial output" in resp.message
    assert resp.data["exit_code"] == 3


def test_one_shot_rejects_a_language_it_cannot_run(tmp_path):
    """Refused by name rather than attempted and reported as a syntax error, which is
    what handing the code to the wrong interpreter would produce — an error about the
    code, for a problem that has nothing to do with the code."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        CodeInterpreterTool(use_kernel=False)(
            code="print(1)", language="brainfuck", ctx=SimpleNamespace(extra={})
        )
    )
    assert resp.success is False
    assert "Unsupported language" in resp.message


# --------------------------------------------------------------------------- #
# A timeout says what happened and what to do about it
# --------------------------------------------------------------------------- #
def test_a_timeout_names_the_cause_and_suggests_the_fix(tmp_path):
    """A bare TimeoutError once reached the generic handler, whose str() is empty, so
    the agent received the literal "Error executing command: " and could not tell a
    timeout from a crash. Seen when `./executable -z` on a reconstruction that fell into
    its TUI loop blocked for the full timeout and the agent learned nothing."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access", timeout=1)(
            command="sleep 30", ctx=SimpleNamespace(extra={})
        )
    )
    assert resp.success is False
    assert "timed out" in resp.message
    # The agent must be able to act on it next time.
    assert "timeout 2" in resp.message
    assert "124" in resp.message


# --------------------------------------------------------------------------- #
# Sandbox output keeps the line structure it was written with
# --------------------------------------------------------------------------- #
#
# opensandbox returns ExecutionLogs.stdout as a list of OutputMessage, one per line
# with the trailing newline stripped. _logs_to_str joined them with "" on the
# assumption they were raw stream chunks, so every sandboxed command came back as a
# single run-together line: `ls -la` unreadable, and a program's 21-line `--help`
# collapsed to 1. Anything downstream that split on newlines (glob_search's find
# parsing, any diff the agent tried between two programs' output) was operating on
# glued-together text.


class _Msg:
    """Stand-in for opensandbox's OutputMessage."""

    def __init__(self, text):
        self.text = text


def test_sandbox_logs_rejoin_lines_with_newlines():
    """The chunks look like stream fragments and are not: they are lines with the
    terminator already removed. Joining them the way raw chunks would be joined is the
    single most plausible wrong reading, and it produces text that is not corrupt, merely
    all on one line — `ls -la` unreadable, a 21-line `--help` collapsed to one, and
    anything downstream that splits on newlines working from glued-together text."""
    from agentevolver.sandbox.default.base import _logs_to_str

    # printf 'A\nB\nC\n' comes back as three chunks, newlines already stripped.
    assert _logs_to_str([_Msg("A"), _Msg("B"), _Msg("C")]) == "A\nB\nC"


def test_sandbox_logs_do_not_double_a_blank_line():
    """A blank line arrives as a chunk whose text is exactly "\\n" — joining naively
    would turn one empty line into two."""
    from agentevolver.sandbox.default.base import _logs_to_str

    assert _logs_to_str([_Msg("X"), _Msg("\n"), _Msg("Y")]) == "X\n\nY"


def test_sandbox_logs_preserve_leading_whitespace():
    """ProgramBench grades `--help` text exactly, indentation included, so the
    rejoin must not strip anything but the line terminator."""
    from agentevolver.sandbox.default.base import _logs_to_str

    out = _logs_to_str([_Msg(" Usage: prog [-ab]"), _Msg("  -a: async"), _Msg("")])
    assert out == " Usage: prog [-ab]\n  -a: async\n"


def test_sandbox_logs_pass_through_a_plain_string():
    """The field is typed as either a string or a list of chunks, and which one arrives
    depends on the call. A rejoin that assumed the list form would iterate a string
    character by character and return it with a newline between every letter."""
    from agentevolver.sandbox.default.base import _logs_to_str

    assert _logs_to_str("already\njoined") == "already\njoined"
    assert _logs_to_str(None) == ""


def test_the_assembled_result_keeps_its_line_breaks():
    """The rejoin above is only worth anything if the assembly step actually uses it —
    this is the function every sandboxed command's output travels through on its way to
    the agent, and it is where a second, naive join would live."""
    from agentevolver.sandbox.default.base import execution_to_result

    execution = SimpleNamespace(
        logs=SimpleNamespace(stdout=[_Msg("line one"), _Msg("line two")], stderr=[]),
        exit_code=0,
        result=[],
        error=None,
    )
    assert execution_to_result(execution).stdout == "line one\nline two"


# --------------------------------------------------------------------------- #
# A file a sandbox writes has to be readable by whoever runs it next
# --------------------------------------------------------------------------- #
#
# opensandbox renders the `mode` int in decimal and parses that string as base 8, so a
# genuine 0o644 arrived as "420" -> 0o420 (-r---w----) and 0o755 arrived as "493",
# failing the call outright. Every file written through write_file_tool/edit_file_tool
# was 0o420; the mode survives the submission tarball, and the ProgramBench images run
# as the non-root user `agent`, so the graded build could not read its own source.


def test_sandbox_write_file_sends_octal_digits_not_the_int():
    """The wire format is a decimal-looking integer that the server parses as base 8, so
    the number that has to go out is the one a human would *write*, not the one Python
    means. Sending `0o644` renders as "420" and arrives as 0o420 — a file the owner
    cannot read — while `0o755` renders as "493" and fails the call outright."""
    from agentevolver.sandbox.default.base import OpenSandbox

    sent = {}

    class _Files:
        async def write_file(self, path, data, mode=None):
            sent[path] = mode

    sandbox = OpenSandbox.__new__(OpenSandbox)
    sandbox._require = lambda: SimpleNamespace(files=_Files())

    asyncio.run(sandbox.write_file("/workspace/a.c", "int main(){}"))
    asyncio.run(sandbox.write_file("/workspace/b.sh", "echo hi", mode=0o755))

    # Octal *digits* — NOT 511/493, which the server would read as 0o511 and as a
    # parse error respectively.
    assert sent["/workspace/a.c"] == 777
    assert sent["/workspace/b.sh"] == 755
    assert sent["/workspace/a.c"] != 0o777


def test_default_sandbox_file_mode_is_permissive():
    """A sandbox is a disposable container we own; the container is the boundary, not
    the file bits. Restrictive modes inside it only produced failures (unreadable
    source at grading time, a compile.sh needing chmod before it could be tested)."""
    from agentevolver.sandbox.types import DEFAULT_FILE_MODE

    assert DEFAULT_FILE_MODE == 0o777


def test_base_sandbox_write_file_applies_the_mode():
    """The base backend accepted `mode` and silently ignored it, so an identical call
    produced different results depending on the backend."""
    from agentevolver.sandbox.types import Sandbox

    ran = []

    class _Shell(Sandbox):
        async def run_command(self, command, **kwargs):
            from agentevolver.sandbox.types import ExecResult  # noqa: PLC0415

            ran.append(command)
            return ExecResult(success=True, exit_code=0)

    sandbox = _Shell.__new__(_Shell)
    asyncio.run(Sandbox.write_file(sandbox, "/workspace/x.c", "int main(){}"))
    assert "chmod 777 /workspace/x.c" in ran[0]


# --------------------------------------------------------------------------- #
# A tool hands back results, and keeps its own belongings out of the workspace
# --------------------------------------------------------------------------- #
def test_grep_search_skips_binaries(tmp_path):
    """A compiled binary can match any pattern by coincidence, and the hit is a line of
    mojibake. Searching a workspace holding a reference executable for "Usage" returned
    three matches, one of them a screenful of bytes — a plausible-looking answer the
    agent then has to spend a turn discarding."""
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "notes.txt").write_text("Usage: prog [-a]\n")
    (tmp_path / "executable").write_bytes(b"\x7fELF\x00\x00Usage\x00\xff\xfe binary noise")
    config.workspace_root = str(tmp_path)

    resp = asyncio.run(
        GrepSearchTool(permission_mode="danger_full_access")(
            pattern="Usage", root=str(tmp_path), ctx=SimpleNamespace(extra={})
        )
    )
    assert resp.success is True
    assert [r["file"].split("/")[-1] for r in resp.data["results"]] == ["notes.txt"]


def test_a_tools_own_bookkeeping_stays_out_of_the_deliverable(tmp_path):
    """A tool's state belongs under <log_root>/tool, never in the workspace.

    Defaulting into the workspace put a tool's directory in the middle of the user's
    deliverable — and where a run's workspace gets packaged up, shipped it along with
    the work. Written against `todo_tool` originally; `journal_tool` carries the same
    kind of state and the rule is the tool's, not that tool's.

    `plan.md` is the deliberate exception and lives in the workspace: it is not the
    framework's bookkeeping but a document the agent writes and the person reads, and
    `workspace_write` is the permission the agent holds. See
    `test_the_agent_is_allowed_to_write_its_own_plan`.
    """
    import agentevolver.tool.default  # noqa: F401 — importing is what registers them
    from agentevolver.registry import TOOL

    config.workspace_root = str(tmp_path / "workspace")
    config.log_root = str(tmp_path / "log")
    os.makedirs(config.workspace_root, exist_ok=True)

    # Across the registry rather than one named tool. The original named `todo_tool` and
    # died with it; the rule is every tool's, and the next tool to grow a `base_dir` is
    # the one that needs checking.
    offenders = []
    for name, cls in sorted(TOOL.module_dict.items()):
        if "base_dir" not in getattr(cls, "model_fields", {}):
            continue
        try:
            instance = cls()
        except Exception:  # noqa: BLE001 — needs args
            continue
        if config.workspace_root in str(getattr(instance, "base_dir", "") or ""):
            offenders.append(f"{name} keeps state at {instance.base_dir}")

    assert not offenders, "tool state inside the deliverable:\n  " + "\n  ".join(offenders)
    assert os.listdir(config.workspace_root) == [], (
        "constructing the tools wrote into the workspace"
    )


# --------------------------------------------------------------------------- #
# One command's output cannot cost the run its context window
# --------------------------------------------------------------------------- #
#
# A `strings` call against a 31KB binary returned 14,419,441 characters. That result was
# handed to the agent whole and stored in its memory, so every turn afterwards asked for
# ~4.3M tokens against a 1,048,576 limit; the run died of consecutive 400s and the
# reported cause named neither the command nor the size.


def test_clip_output_leaves_short_output_alone():
    """Almost every command is this case. A clipper that appended its notice
    unconditionally would tell the agent that ordinary, complete output was an excerpt,
    and invite it to re-run narrower commands to recover text it already had."""
    from agentevolver.tool.types import clip_output

    assert clip_output("short") == "short"


def test_clip_output_keeps_the_beginning_and_the_end():
    """The head says what the command set out to do, the tail says how it ended — the
    error, the summary, the exit status. The middle of an oversized dump is the least
    informative part."""
    from agentevolver.tool.types import clip_output

    text = "HEAD-MARKER" + ("x" * 200_000) + "TAIL-MARKER"
    clipped = clip_output(text, limit=1_000)

    assert clipped.startswith("HEAD-MARKER")
    assert clipped.endswith("TAIL-MARKER")
    assert len(clipped) < 1_500
    # And it says what is missing, so the agent narrows the command instead of wondering
    # why the text stops.
    assert "characters elided" in clipped
    assert "Narrow the command" in clipped


def test_bash_flood_is_clipped_by_the_pipeline(tmp_path):
    """The bound is applied once, in the dispatch funnel, not in each tool.

    The tool returns what it captured; ``ToolContextManager`` is what decides the
    result is too large to show, spills it, and hands back the excerpt. Asserted
    through the manager for that reason — a tool called directly is *expected* to
    return its full output now.
    """
    from agentevolver.tool.types import OUTPUT_LIMIT

    config.workspace_root = str(tmp_path)
    manager = _manager_for(tmp_path, BashTool(permission_mode="danger_full_access"))
    resp = asyncio.run(
        manager(
            name="bash_tool",
            input={"command": "python3 -c \"print('A' * 3_000_000)\""},
            ctx=SimpleNamespace(id="call-1", extra={}),
        )
    )

    assert resp.success is True
    assert len(resp.message) < OUTPUT_LIMIT * 2
    assert "omitted inline as one complete unit" in resp.message


def test_bash_clips_each_stream_separately(tmp_path):
    """A command that floods stdout must not cost the agent the stderr explaining why."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="python3 -c \"import sys; print('A'*3_000_000); sys.stderr.write('THE REASON')\"",
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert "THE REASON" in resp.message


def test_memory_caps_one_entry_even_if_a_tool_does_not():
    """The backstop. Memory holds a window of these and renders all of it into every
    later prompt, so what a turn can afford to read once, a prompt cannot afford to
    carry forever."""
    from agentevolver.memory.default.tiered import _RECORD_DETAIL_MAX, MemoryRecord, TieredMemory

    class _State:
        def __init__(self):
            self.recent = []
            self._compacting = True  # keep _compact() out of it

    memory = TieredMemory(base_dir="/tmp", recent_max=100)
    state = _State()

    TieredMemory._append_recent(
        memory,
        state,
        MemoryRecord(
            ts="00:00:00", event="bash_tool result", detail="Z" * 14_419_441, status="done"
        ),
    )

    stored = state.recent[0].detail
    assert len(stored) < _RECORD_DETAIL_MAX + 200
    assert "Exact detail omitted inline as one complete unit" in stored
    assert _RECORD_DETAIL_MAX < 32_000, "must be tighter than a single tool's own limit"


# --------------------------------------------------------------------------- #
# A shell with no terminal cannot observe terminal behaviour
# --------------------------------------------------------------------------- #
#
# A program that draws a screen, prompts, or colourises takes a different path when its
# output is not a terminal — usually refusing to start. Comparing two such programs
# without one compares two refusals, and they agree. An entire class of a reference's
# behaviour was invisible this way: 12 of the graded test files drive the program through
# a pty, and the reconstruction that never implemented any of it looked correct.


def test_tty_is_off_by_default(tmp_path):
    """Turning it on unconditionally would change what every other command reports:
    programs check isatty() and colourise, paginate, or prompt when they think a human is
    watching."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command='python3 -c "import sys; print(sys.stdout.isatty())"',
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert "False" in resp.message


def test_tty_gives_the_command_a_terminal(tmp_path):
    """`isatty()` is the check the programs under study make, so it is the one that
    decides whether a full-screen program draws anything at all. Any weaker arrangement —
    a pipe, a pty that is not on the program's own stdout — leaves it refusing to start,
    which reads as "this program does nothing"."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command='python3 -c "import sys; print(sys.stdout.isatty())"',
            tty=True,
            timeout=10,
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert resp.success is True
    assert "True" in resp.message


def test_tty_sets_a_terminal_type(tmp_path):
    """A terminal device is not enough on its own: anything built on curses also looks
    TERM up in terminfo, and without it reports "Error opening terminal: unknown" and
    exits — the same refusal that having no terminal produces, which is the thing this
    exists to get past."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="echo TERM=$TERM", tty=True, timeout=10, ctx=SimpleNamespace(extra={})
        )
    )
    assert "TERM=" in resp.message
    assert "TERM=\r\n" not in resp.message, "TERM was left empty"


def test_a_caller_can_choose_the_terminal_type(tmp_path):
    """How a program behaves under a different TERM is itself worth comparing — the graded
    tests use linux, vt100, dumb, screen and a name that does not exist."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="TERM=vt100 sh -c 'echo TERM=$TERM'",
            tty=True,
            timeout=10,
            ctx=SimpleNamespace(extra={}),
        )
    )
    assert "TERM=vt100" in resp.message


def test_stdin_reaches_the_command(tmp_path):
    """Driving an interactive program means sending it keys — and being able to tell it to
    quit, which is the difference between a comparison and a hang."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="cat", stdin="hello from stdin\n", timeout=10, ctx=SimpleNamespace(extra={})
        )
    )
    assert "hello from stdin" in resp.message


def test_a_tty_command_that_never_exits_times_out_with_advice(tmp_path):
    """A full-screen program holds the terminal until told to leave."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="sleep 30", tty=True, timeout=2, ctx=SimpleNamespace(extra={})
        )
    )
    assert resp.success is False
    assert resp.data["timed_out"] is True
    assert "whatever key quits it" in resp.message


def test_a_per_call_timeout_overrides_the_default(tmp_path):
    """The tool's own default is generous on purpose — a build needs it. A caller that
    knows this particular command should finish in a second is the one who can say so,
    and without the override every probe of a program that hangs costs the full default
    before the agent learns anything."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access", timeout=600)(
            command="sleep 30", timeout=1, ctx=SimpleNamespace(extra={})
        )
    )
    assert resp.success is False
    assert "timed out after 1 seconds" in resp.message


# --------------------------------------------------------------------------- #
# A terminal displays a screen, not a byte stream
# --------------------------------------------------------------------------- #
#
# The bytes a full-screen program writes are instructions to a device — move here, set
# this colour, erase to end of line. What a person sees is the screen those instructions
# leave behind. Handing over the raw stream hands over the wire protocol instead of the
# page: one reference program's 500-character screen arrived as 32,184 bytes of escape
# sequences, which filled the output budget and got skimmed past as noise.


def test_the_screen_is_returned_not_the_escape_sequences():
    """The cursor move is why stripping escapes is not enough: "there" is written at row
    5, column 10, so its position is information a filter would discard while keeping the
    word. Interpreting the stream is the only way to end up with what was displayed."""
    data = b"\x1b[2J\x1b[H" + b"hello" + b"\x1b[5;10Hthere"
    out = render_terminal(data)
    assert "hello" in out and "there" in out
    assert "\x1b" not in out


def test_a_redrawn_screen_collapses_to_what_it_ends_up_showing():
    """The point of rendering: a program that repaints reports its screen, not every frame
    it painted to get there."""
    frames = b"".join(b"\x1b[2J\x1b[H" + f"frame {i}".encode() for i in range(500))
    out = render_terminal(frames)
    assert "frame 499" in out
    assert "frame 498" not in out
    assert len(out) < len(frames) / 10


def test_colour_and_boldness_are_reported_not_dropped():
    """For a program whose whole job is how it draws, "it is red" is the observation — and
    the reference's -C flag is only visible this way."""
    out = render_terminal(b"\x1b[31mR\x1b[0m \x1b[1;37mB\x1b[0m")
    assert "red" in out and "white bold" in out


def test_a_screen_that_uses_no_colour_says_nothing_about_colour():
    """The colour summary is an observation about the program, so it must not appear for
    a program that made none. A default that reported the terminal's own foreground would
    have the agent comparing two programs on an attribute neither one set."""
    out = render_terminal(b"plain text\r\n")
    assert "plain text" in out
    assert "red" not in out and "green" not in out


def test_output_longer_than_the_screen_keeps_its_scrollback():
    """Rendering must not cost a line-oriented command its output: 60 lines of build log
    through a 24-row terminal is still 60 lines."""
    out = render_terminal(b"".join(f"line {i}\r\n".encode() for i in range(60)))
    assert "line 0" in out
    assert "line 59" in out


def test_a_tty_command_reports_the_screen(tmp_path):
    """End to end through the tool, not the renderer alone: the pty path has to hand its
    bytes to the renderer, and the dimensions are stated because a screen is only
    interpretable against the size it was laid out for."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command="printf 'a\\nb\\n'", tty=True, timeout=10, ctx=SimpleNamespace(extra={})
        )
    )
    assert "a\nb" in resp.message
    assert "terminal 80x24" in resp.message


def test_a_program_that_clears_on_exit_still_reports_what_it_drew():
    """A curses program's exit path hands the terminal back the way it found it — clear the
    screen, restore the cursor, leave. So the final frame is blank by design, and reporting
    only the end state says "this program displays nothing" about a program that displayed
    plenty. That is worse than raw bytes, because it looks like an answer."""
    drew_then_left = b"\x1b[?1049h" + b"falling characters" + b"\x1b[2J\x1b[H" + b"\x1b[?1049l"
    out = render_terminal(drew_then_left)
    assert "falling characters" in out
    assert "as it appeared while running" in out


def test_a_screen_that_ends_with_content_is_not_labelled_as_cleared():
    """The counterweight to rescuing the fullest frame. An ordinary command's output is
    its final state, and labelling that "as it appeared while running" would suggest the
    program cleared up after itself when it never drew a screen at all."""
    out = render_terminal(b"still here\r\n")
    assert "still here" in out
    assert "as it appeared while running" not in out


def test_keystrokes_wait_for_the_program_to_draw(tmp_path):
    """`q` delivered at once quits a full-screen program before it paints, and the empty
    screen that comes back reads as "it displays nothing" — which is how a reconstruction
    that drew nothing came to look correct."""
    config.workspace_root = str(tmp_path)
    script = tmp_path / "draws.sh"
    script.write_text("#!/bin/bash\nprintf 'PAINTED'\nread -n1 key\nprintf '\\033[2J\\033[H'\n")
    script.chmod(0o755)
    resp = asyncio.run(
        BashTool(permission_mode="danger_full_access")(
            command=str(script), tty=True, stdin="q", timeout=8, ctx=SimpleNamespace(extra={})
        )
    )
    assert "PAINTED" in resp.message
    assert resp.success is True, "the key must still be delivered, not withheld until timeout"


def test_a_line_left_on_the_restored_screen_does_not_hide_the_drawing():
    """What a program drew is routinely followed by a line or two on the screen it handed
    back — a shell prompt, an `echo exit=$?`. Rescuing only an entirely blank final frame
    let one such line make a screenful of drawing look like it never happened."""
    out = render_terminal(
        b"\x1b[?1049h"
        + b"\r\n".join(f"rain row {i}".encode() for i in range(10))
        + b"\x1b[2J\x1b[H\x1b[?1049l"
        + b"exit=0\r\n"
    )
    assert "rain row 9" in out
    assert "exit=0" in out
    assert "as it appeared while running" in out
