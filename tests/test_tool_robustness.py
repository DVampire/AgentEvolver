import os
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
# Fixes #4-#7 — one execution path, and it keeps the contract
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
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="echo 'nothing added to commit but untracked files present'; exit 1",
        ctx=SimpleNamespace(extra={}),
    ))
    assert resp.success is True
    assert resp.data["exit_code"] == 1
    assert "nothing added to commit" in resp.message


def test_a_command_that_cannot_run_at_all_is_a_tool_failure(tmp_path):
    """The line between the two: a command that ran and returned a verdict is an
    observation; being unable to run one is a failure."""
    config.workspace_root = str(tmp_path / "does-not-exist")
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="echo hi", ctx=SimpleNamespace(extra={})))
    assert resp.success is False


def test_search_tools_report_no_matches_as_an_answer(tmp_path):
    """grep exiting 1 for "found nothing" is a result, not an error."""
    from agentevolver.tool.default.glob_search import GlobSearchTool
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "a.c").write_text("int rows = 0;\n")
    config.workspace_root = str(tmp_path)
    ctx = SimpleNamespace(extra={})

    r = asyncio.run(GrepSearchTool(permission_mode="danger_full_access")(
        pattern="nothing matches this", root=str(tmp_path), ctx=ctx))
    assert r.success is True
    assert r.data["results"] == []
    assert "No matches" in r.message

    g = asyncio.run(GlobSearchTool(permission_mode="danger_full_access")(
        pattern="*.nope", root=str(tmp_path), ctx=ctx))
    assert g.success is True
    assert g.data["matches"] == []


def test_search_tools_find_what_is_there(tmp_path):
    from agentevolver.tool.default.glob_search import GlobSearchTool
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "a.c").write_text("int rows = 0;\n")
    config.workspace_root = str(tmp_path)
    ctx = SimpleNamespace(extra={})

    g = asyncio.run(GlobSearchTool(permission_mode="danger_full_access")(
        pattern="*.c", root=str(tmp_path), ctx=ctx))
    assert g.success is True and len(g.data["matches"]) == 1

    r = asyncio.run(GrepSearchTool(permission_mode="danger_full_access")(
        pattern="int rows", root=str(tmp_path), ctx=ctx))
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

    resp = asyncio.run(ListDirTool()(
        path=str(tmp_path), ignore=["target"], ctx=SimpleNamespace(extra={})))
    assert "keep.c" in resp.message
    assert "target" not in resp.message


# --------------------------------------------------------------------------- #
# code_interpreter: kernel by default, one-shot when the kernel is the wrong lens
# --------------------------------------------------------------------------- #
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


def test_one_shot_sees_the_filesystem_as_it_is_now(tmp_path):
    """The reason one-shot exists: a kernel held open from before a file appeared
    answered FileNotFoundError to the very script that would have used it."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    (tmp_path / "target.c").write_text("int main(){}\n")
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(CodeInterpreterTool(use_kernel=False)(
        code="import pathlib; print('found:', pathlib.Path('target.c').exists())",
        ctx=SimpleNamespace(extra={}),
    ))
    assert resp.success is True
    assert "found: True" in resp.message


def test_one_shot_nonzero_exit_is_an_observation(tmp_path):
    """A script that ran and failed has told the agent something; calling that a tool
    malfunction hides the output it needs."""
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    config.workspace_root = str(tmp_path)
    resp = asyncio.run(CodeInterpreterTool(use_kernel=False)(
        code="import sys; print('partial output'); sys.exit(3)",
        ctx=SimpleNamespace(extra={}),
    ))
    assert resp.success is True
    assert "partial output" in resp.message
    assert resp.data["exit_code"] == 3


def test_one_shot_rejects_a_language_it_cannot_run(tmp_path):
    from agentevolver.tool.default.code_interpreter import CodeInterpreterTool

    config.workspace_root = str(tmp_path)
    resp = asyncio.run(CodeInterpreterTool(use_kernel=False)(
        code="print(1)", language="brainfuck", ctx=SimpleNamespace(extra={})))
    assert resp.success is False
    assert "Unsupported language" in resp.message


# --------------------------------------------------------------------------- #
# Fix #7 — a timeout says what happened and what to do about it
# --------------------------------------------------------------------------- #
def test_a_timeout_names_the_cause_and_suggests_the_fix(tmp_path):
    """A bare TimeoutError once reached the generic handler, whose str() is empty, so
    the agent received the literal "Error executing command: " and could not tell a
    timeout from a crash. Seen when `./executable -z` on a reconstruction that fell into
    its TUI loop blocked for the full timeout and the agent learned nothing."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access", timeout=1)(
        command="sleep 30", ctx=SimpleNamespace(extra={})))
    assert resp.success is False
    assert "timed out" in resp.message
    # The agent must be able to act on it next time.
    assert "timeout 2" in resp.message
    assert "124" in resp.message


# --- fix #8: sandbox command output had its line structure destroyed -------------
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
    from agentevolver.sandbox.default.base import _logs_to_str

    assert _logs_to_str("already\njoined") == "already\njoined"
    assert _logs_to_str(None) == ""


def test_execution_to_result_keeps_multiline_stdout():
    from agentevolver.sandbox.default.base import execution_to_result

    execution = SimpleNamespace(
        logs=SimpleNamespace(stdout=[_Msg("line one"), _Msg("line two")], stderr=[]),
        exit_code=0, result=[], error=None,
    )
    assert execution_to_result(execution).stdout == "line one\nline two"


# --- fix #10: sandbox writes produced unreadable files ---------------------------
#
# opensandbox renders the `mode` int in decimal and parses that string as base 8, so a
# genuine 0o644 arrived as "420" -> 0o420 (-r---w----) and 0o755 arrived as "493",
# failing the call outright. Every file written through write_file_tool/edit_file_tool
# was 0o420; the mode survives the submission tarball, and the ProgramBench images run
# as the non-root user `agent`, so the graded build could not read its own source.

def test_sandbox_write_file_sends_octal_digits_not_the_int():
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


def test_grep_search_skips_binaries(tmp_path):
    """A compiled binary can match any pattern by coincidence, and the hit is a line of
    mojibake. Searching a workspace holding a reference executable for "Usage" returned
    three matches, one of them a screenful of bytes — a plausible-looking answer the
    agent then has to spend a turn discarding."""
    from agentevolver.tool.default.grep_search import GrepSearchTool

    (tmp_path / "notes.txt").write_text("Usage: prog [-a]\n")
    (tmp_path / "executable").write_bytes(b"\x7fELF\x00\x00Usage\x00\xff\xfe binary noise")
    config.workspace_root = str(tmp_path)

    resp = asyncio.run(GrepSearchTool(permission_mode="danger_full_access")(
        pattern="Usage", root=str(tmp_path), ctx=SimpleNamespace(extra={})))
    assert resp.success is True
    assert [r["file"].split("/")[-1] for r in resp.data["results"]] == ["notes.txt"]


def test_todo_state_lives_with_the_other_tools_state(tmp_path):
    """A todo list is the tool's own bookkeeping, and every other tool keeps its state
    under <log_root>/tool. Defaulting into the workspace put a `todo_tool/` directory in
    the middle of the user's deliverable — and where a run's workspace gets packaged up,
    shipped it along with the work."""
    from agentevolver.tool.default.todo import TodoTool

    config.workspace_root = str(tmp_path / "workspace")
    config.log_root = str(tmp_path / "log")
    os.makedirs(config.workspace_root, exist_ok=True)

    tool = TodoTool()
    assert tool.base_dir == os.path.join(config.log_root, "tool", "todo_tool")
    assert config.workspace_root not in tool.base_dir
    # Nothing of ours appears in the deliverable directory.
    assert os.listdir(config.workspace_root) == []


# --- fix #11: one command's output could make the prompt unsendable --------------
#
# A `strings` call against a 31KB binary returned 14,419,441 characters. That result was
# handed to the agent whole and stored in its memory, so every turn afterwards asked for
# ~4.3M tokens against a 1,048,576 limit; the run died of consecutive 400s and the
# reported cause named neither the command nor the size.

def test_clip_output_leaves_short_output_alone():
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


def test_bash_clips_a_flood_of_output(tmp_path):
    from agentevolver.tool.types import OUTPUT_LIMIT

    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="python3 -c \"print('A' * 3_000_000)\"", ctx=SimpleNamespace(extra={})))

    assert resp.success is True
    assert len(resp.message) < OUTPUT_LIMIT * 2
    assert "characters elided" in resp.message


def test_bash_clips_each_stream_separately(tmp_path):
    """A command that floods stdout must not cost the agent the stderr explaining why."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="python3 -c \"import sys; print('A'*3_000_000); sys.stderr.write('THE REASON')\"",
        ctx=SimpleNamespace(extra={})))
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

    TieredMemory._append_recent(memory, state, MemoryRecord(
        ts="00:00:00", event="bash_tool result", detail="Z" * 14_419_441, status="done"))

    stored = state.recent[0].detail
    assert len(stored) < _RECORD_DETAIL_MAX + 200
    assert "more characters not kept in memory" in stored
    assert _RECORD_DETAIL_MAX < 32_000, "must be tighter than a single tool's own limit"


# --- fix #12: a shell with no terminal cannot observe terminal behaviour ----------
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
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="python3 -c \"import sys; print(sys.stdout.isatty())\"",
        ctx=SimpleNamespace(extra={})))
    assert "False" in resp.message


def test_tty_gives_the_command_a_terminal(tmp_path):
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="python3 -c \"import sys; print(sys.stdout.isatty())\"",
        tty=True, timeout=10, ctx=SimpleNamespace(extra={})))
    assert resp.success is True
    assert "True" in resp.message


def test_tty_sets_a_terminal_type(tmp_path):
    """A terminal device is not enough on its own: anything built on curses also looks
    TERM up in terminfo, and without it reports "Error opening terminal: unknown" and
    exits — the same refusal that having no terminal produces, which is the thing this
    exists to get past."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="echo TERM=$TERM", tty=True, timeout=10, ctx=SimpleNamespace(extra={})))
    assert "TERM=" in resp.message
    assert "TERM=\r\n" not in resp.message, "TERM was left empty"


def test_a_caller_can_choose_the_terminal_type(tmp_path):
    """How a program behaves under a different TERM is itself worth comparing — the graded
    tests use linux, vt100, dumb, screen and a name that does not exist."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="TERM=vt100 sh -c 'echo TERM=$TERM'", tty=True, timeout=10,
        ctx=SimpleNamespace(extra={})))
    assert "TERM=vt100" in resp.message


def test_stdin_reaches_the_command(tmp_path):
    """Driving an interactive program means sending it keys — and being able to tell it to
    quit, which is the difference between a comparison and a hang."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="cat", stdin="hello from stdin\n", timeout=10,
        ctx=SimpleNamespace(extra={})))
    assert "hello from stdin" in resp.message


def test_a_tty_command_that_never_exits_times_out_with_advice(tmp_path):
    """A full-screen program holds the terminal until told to leave."""
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="sleep 30", tty=True, timeout=2, ctx=SimpleNamespace(extra={})))
    assert resp.success is False
    assert resp.data["timed_out"] is True
    assert "whatever key quits it" in resp.message


def test_a_per_call_timeout_overrides_the_default(tmp_path):
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access", timeout=600)(
        command="sleep 30", timeout=1, ctx=SimpleNamespace(extra={})))
    assert resp.success is False
    assert "timed out after 1 seconds" in resp.message


# --- fix #13: a terminal displays a screen, not a byte stream ---------------------
#
# The bytes a full-screen program writes are instructions to a device — move here, set
# this colour, erase to end of line. What a person sees is the screen those instructions
# leave behind. Handing over the raw stream hands over the wire protocol instead of the
# page: one reference program's 500-character screen arrived as 32,184 bytes of escape
# sequences, which filled the output budget and got skimmed past as noise.

def test_the_screen_is_returned_not_the_escape_sequences():
    from agentevolver.tool.default.bash import _render_terminal
    data = b"\x1b[2J\x1b[H" + b"hello" + b"\x1b[5;10Hthere"
    out = _render_terminal(data)
    assert "hello" in out and "there" in out
    assert "\x1b" not in out


def test_a_redrawn_screen_collapses_to_what_it_ends_up_showing():
    """The point of rendering: a program that repaints reports its screen, not every frame
    it painted to get there."""
    from agentevolver.tool.default.bash import _render_terminal
    frames = b"".join(b"\x1b[2J\x1b[H" + f"frame {i}".encode() for i in range(500))
    out = _render_terminal(frames)
    assert "frame 499" in out
    assert "frame 498" not in out
    assert len(out) < len(frames) / 10


def test_colour_and_boldness_are_reported_not_dropped():
    """For a program whose whole job is how it draws, "it is red" is the observation — and
    the reference's -C flag is only visible this way."""
    from agentevolver.tool.default.bash import _render_terminal
    out = _render_terminal(b"\x1b[31mR\x1b[0m \x1b[1;37mB\x1b[0m")
    assert "red" in out and "white bold" in out


def test_a_screen_that_uses_no_colour_says_nothing_about_colour():
    from agentevolver.tool.default.bash import _render_terminal
    out = _render_terminal(b"plain text\r\n")
    assert "plain text" in out
    assert "red" not in out and "green" not in out


def test_output_longer_than_the_screen_keeps_its_scrollback():
    """Rendering must not cost a line-oriented command its output: 60 lines of build log
    through a 24-row terminal is still 60 lines."""
    from agentevolver.tool.default.bash import _render_terminal
    out = _render_terminal(b"".join(f"line {i}\r\n".encode() for i in range(60)))
    assert "line 0" in out
    assert "line 59" in out


def test_a_tty_command_reports_the_screen(tmp_path):
    config.workspace_root = str(tmp_path)
    resp = asyncio.run(BashTool(permission_mode="danger_full_access")(
        command="printf 'a\\nb\\n'", tty=True, timeout=10, ctx=SimpleNamespace(extra={})))
    assert "a\nb" in resp.message
    assert "terminal 80x24" in resp.message
