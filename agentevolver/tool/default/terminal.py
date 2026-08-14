"""The six tools that drive a terminal which outlives one call: open, send, read, list, signal, close.

Every `bash_tool` call is a fresh process, so everything a command changed about its own
environment is gone by the next one: the directory it moved to, the virtualenv it
activated, the host it ssh'd into, the interpreter it started. These tools hand the agent
a shell that stays where it was left, and the tools that follow are the ones a person
needs at a terminal — type at it, look at it without typing, interrupt what is running,
and put it away.
"""

from typing import Any, Dict, Optional

from pydantic import Field

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.terminal import (
    ALLOWED_SIGNALS,
    DEFAULT_SEND_TIMEOUT,
    TerminalBusy,
    WaitReason,
    terminal_manager,
)
from agentevolver.tool.types import Tool, clip_output

_OPEN_DESCRIPTION = "Open a terminal that stays alive between calls."
_OPEN_INSTRUCTION = """
## Function
Start a shell in a real terminal and keep it. Unlike `bash_tool`, which runs each command
in a new process, everything this shell does to itself persists: a `cd`, an activated
virtualenv, exported variables, an `ssh` session, a `python`/`psql`/`gdb` prompt you are
part-way through.

Use it when the state between commands is the point. For a single command that stands on
its own, `bash_tool` is cheaper — it costs one call rather than an open, a send and a
close.

You get back the terminal id and what the terminal shows once the shell has settled. Track
the id; close the terminal when the work it holds is done.

## Parameters
- name (str, optional): A label for it, unique among your open terminals — "build",
  "db", "remote". Listings show it, so a terminal can be recognised without reading it.
- command (str, optional): What to run in the terminal instead of a shell — `python3 -i`,
  `ssh host`, `gdb ./program`. The terminal ends when this program does.
- cwd (str, optional): Where it starts. Defaults to the workspace root.

## Example
{"name": "terminal_open_tool", "args": {"name": "build"}}
{"name": "terminal_open_tool", "args": {"name": "repl", "command": "python3 -i"}}
"""

_SEND_DESCRIPTION = "Type into an open terminal and read what appears."
_SEND_INSTRUCTION = """
## Function
Write text to a terminal and wait for it to go quiet, then return what appeared because of
it — not the whole screen, only what changed.

Read the wait reason. A terminal has nothing that says "the command finished": the shell
is still there either way, so silence is the only available evidence.
- `idle` — the terminal stopped printing. Usually done; a command that pauses mid-way
  looks identical, so if the output stops somewhere implausible, send an empty text
  (`text: ""`, `submit: false`) to look again.
- `timeout` — still printing when the budget ran out. Nothing is lost: the command keeps
  running and the output keeps accumulating, so read it or send again.
- `exited` — the shell itself is gone. Its output is still readable; open a new terminal.

## Parameters
- terminal_id (str): From `terminal_open_tool` or `terminal_list_tool`.
- text (str): What to type. An empty text with `submit: false` waits and looks without
  disturbing anything.
- submit (bool, optional): Press Enter afterwards. Default true. Set false to send control
  characters ("\\u0003" is ctrl-C) or a line you are still building.
- timeout (int, optional): Seconds to wait for quiet. Default 30.
- run_in_background (bool, optional): Type the command and return at once, watching
  it as a job instead of holding the turn. Use it for anything that runs for
  minutes — the foreground wait settles on silence, and silence is the wrong signal
  there: a build that prints nothing for a stretch looks finished. Collect it with
  `job_output_tool`, or look at the live screen with `terminal_read_tool`.
  `job_kill_tool` stops *watching*; the command keeps running, and
  `terminal_signal_tool` is what stops that. Default false.

## Example
{"name": "terminal_send_tool", "args": {"terminal_id": "term_1a2b3c4d", "text": "cd src && ls"}}
{"name": "terminal_send_tool", "args": {"terminal_id": "term_1a2b3c4d", "text": "", "submit": false}}
"""

_READ_DESCRIPTION = "Read a terminal's output without typing at it."
_READ_INSTRUCTION = """
## Function
Show what a terminal holds — the screen and the lines that have scrolled off it — without
sending anything. Use it on a terminal that is producing output on its own (a build, a
server, a watcher), and to go back over output that has scrolled away.

## Parameters
- terminal_id (str): The terminal to read.
- offset (int, optional): How many lines back from the newest to stop. 0 (default) ends at
  the newest line; 50 shows what was on screen 50 lines ago.
- count (int, optional): How many lines to return. Default 200.

## Example
{"name": "terminal_read_tool", "args": {"terminal_id": "term_1a2b3c4d"}}
{"name": "terminal_read_tool", "args": {"terminal_id": "term_1a2b3c4d", "offset": 200, "count": 100}}
"""

_LIST_DESCRIPTION = "List the terminals this session has open."
_LIST_INSTRUCTION = """
## Function
Every terminal you opened, oldest first, with its label, whether it is still alive, and how
long it has been there. Use it to recover an id you did not keep, and before opening
another — a terminal you forgot about is still holding a shell.

## Parameters
(none)

## Example
{"name": "terminal_list_tool", "args": {}}
"""

_SIGNAL_DESCRIPTION = "Interrupt whatever is running in a terminal."
_SIGNAL_INSTRUCTION = """
## Function
Send a signal to the command currently running in a terminal — what ctrl-C does. The
signal goes to the running command, not to the shell, so the terminal survives and keeps
everything it was holding.

If nothing is running, there is nothing to interrupt: SIGTERM, SIGHUP and SIGKILL are
refused in that state rather than quietly killing the shell. Ending the terminal is
`terminal_close_tool`, which is a different intention.

## Parameters
- terminal_id (str): The terminal whose foreground command to signal.
- signal (str): One of SIGINT, SIGTERM, SIGQUIT, SIGHUP, SIGTSTP, SIGCONT, SIGKILL.
  SIGINT is the ordinary "stop this"; SIGKILL is for something that ignores the rest.

## Example
{"name": "terminal_signal_tool", "args": {"terminal_id": "term_1a2b3c4d", "signal": "SIGINT"}}
"""

_CLOSE_DESCRIPTION = "Close a terminal and everything it is running."
_CLOSE_INSTRUCTION = """
## Function
End a terminal you are finished with, along with anything it started. Its state — the
directory, the environment, the REPL — is gone, so close it when the work that needed
that state is done, not between commands.

Closing is not optional housekeeping. A terminal left open is a live shell for the rest of
the run, and one that is running something is still running it.

## Parameters
- terminal_id (str): The terminal to close.

## Example
{"name": "terminal_close_tool", "args": {"terminal_id": "term_1a2b3c4d"}}
"""


def _session_of(kwargs) -> str:
    ctx = kwargs.get("ctx")
    return str(getattr(ctx, "id", "") or "")


def _resolve(terminal_id: str, session_id: str):
    """The terminal, or a Response saying which ids do exist.

    A bare "not found" leaves the agent guessing at a handle it is already holding —
    usually one it mistyped, or one from a terminal that has since been closed.
    """
    terminal = terminal_manager.get(terminal_id)
    if terminal is not None:
        return terminal, None
    known = [t.id for t in terminal_manager.list(session_id)]
    return None, Response(
        type=ResponseType.TOOL, success=False,
        message=(f"No terminal {terminal_id!r}. This session has: "
                 f"{', '.join(known) if known else '(none — open one with terminal_open_tool)'}"),
    )


def _screen_message(header: str, body: str) -> str:
    return f"{header}\n\n{clip_output(body)}" if body else header


#: How long a backgrounded send keeps watching. Not "forever": a shell that never goes
#: quiet would hold the waiter for the life of the process, and a job stuck RUNNING
#: is one the agent waits on and nothing ever finishes. An hour is far past any
#: foreground budget while still being a bound.
BACKGROUND_SEND_TIMEOUT = 3600.0

@TOOL.register_module(force=True)
class TerminalOpenTool(Tool):
    """Start a shell that survives between tool calls."""

    name: str = "terminal_open_tool"
    description: str = _OPEN_DESCRIPTION
    instruction: str = _OPEN_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Starts a shell process.")
    mutates: bool = True
    #: The startup wait below is bounded well under this, so the tool reports its own
    #: diagnostic rather than being cut off naming neither the shell nor the timeout.
    call_timeout_seconds: float = 60.0

    #: How long the shell is given to print its first prompt. Waiting for it here rather
    #: than leaving it to the first send is what keeps a startup banner — or an ssh
    #: password prompt — out of the middle of the first command's output.
    startup_timeout: float = 10.0

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, name: str = "", command: Optional[str] = None,
                       cwd: Optional[str] = None, **kwargs) -> Response:
        request = PermissionRequest(op=Operation.BASH, target=command or "bash -i")
        allowed = permission_manager.check(self.name, request,
                                           workspace=(config.workspace_root or ""))
        if not allowed.allowed:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Permission denied: {allowed.reason}")

        start_in = cwd or config.workspace_root or None
        try:
            terminal = terminal_manager.open(
                name=name, session_id=_session_of(kwargs),
                command=command, cwd=start_in)
        except (RuntimeError, ValueError, OSError) as error:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Could not open a terminal: {error}")

        # An empty send: type nothing, wait for the shell to settle, and take what it
        # printed while starting up.
        opening, _ = await terminal.send("", submit=False, timeout=self.startup_timeout)
        header = (f"Opened {terminal.id}"
                  + (f" ({name})" if name else "")
                  + f" running {terminal.command or 'bash -i'} in {start_in or 'the current directory'}.\n"
                  f"It stays open until you close it: "
                  f"terminal_send_tool(terminal_id=\"{terminal.id}\", text=...) to use it, "
                  f"terminal_close_tool(terminal_id=\"{terminal.id}\") when done.")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=_screen_message(header, opening),
            data=terminal.snapshot(),
        )


@TOOL.register_module(force=True)
class TerminalSendTool(Tool):
    """Type into a terminal and return what it printed."""

    name: str = "terminal_send_tool"
    description: str = _SEND_DESCRIPTION
    instruction: str = _SEND_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Runs whatever is typed.")
    mutates: Optional[bool] = None
    #: Above the send's own budget, so the wait ends with this tool's account of what
    #: happened rather than with the pipeline cutting the call off.
    call_timeout_seconds: float = DEFAULT_SEND_TIMEOUT + 30.0

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def _send_in_background(self, terminal, terminal_id, text, submit,
                                  session_id) -> Response:
        """Type the command and register the wait as a job instead of holding the turn.

        The foreground path settles on quiet, and quiet is the wrong signal for work that
        runs for minutes: a build prints nothing for a while and looks finished, so the
        caller either waits out the whole budget or reads a completion that has not
        happened. Backgrounding makes the ambiguity explicit — the job is running until it
        is not, and the terminal keeps the output either way.

        Registered in the same registry as a background `bash_tool` command, because
        `job_list_tool` answering "what is outstanding" with only some of the outstanding
        things is worse than not answering: a parent working out what it is still waiting
        on would read the gap as nothing.
        """
        import asyncio as _asyncio

        from agentevolver.job import job_manager

        job = job_manager.register(
            kind="terminal", label=f"{terminal_id}: {text[:70]}", session_id=session_id)

        typed = _asyncio.Event()

        async def _wait() -> None:
            # Set before the send, then awaited below. `ensure_future` only *schedules*:
            # a caller that kills in the same turn can cancel this task before it has run
            # a single line, and then the command was never typed at all — the job reports
            # killed, the terminal never saw the text, and the two agree on a thing that
            # did not happen. `send` writes to the pty before its first await, so once
            # this coroutine is running the keystrokes are committed.
            typed.set()
            try:
                # A long budget rather than none. `send` counts a deadline from a float,
                # so `None` is a TypeError — and, swallowed below, it would have finished
                # the job as "failed" the instant it started, with the command running on
                # regardless. Backgrounding still needs *a* bound: without one, a shell
                # that never goes quiet leaves this waiter alive for the life of the
                # process. `job_kill_tool` is the way to stop caring sooner.
                output, reason = await terminal.send(
                    text, submit=submit, timeout=BACKGROUND_SEND_TIMEOUT)
                job_manager.append_output(job.id, output or "")
                job_manager.finish(job.id, exit_code=0 if reason is not None else None)
            except Exception as error:                              # noqa: BLE001
                # A waiter that dies silently leaves the job RUNNING for good, and the
                # agent waits on something nothing will ever finish.
                job_manager.finish(job.id, error=f"terminal wait failed: {error}")

        # The task is the handle, so `job_kill_tool` cancels the wait. It does not stop
        # the command — that is `terminal_signal_tool`'s job, and conflating them would
        # let "stop watching" read as "stop running".
        job.handle = _asyncio.ensure_future(_wait())
        await typed.wait()
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Typed into {terminal_id}; watching it as {job.id}.\n"
                     f"  job_output_tool(job_id=\"{job.id}\")  — what it has printed\n"
                     f"  terminal_read_tool(terminal_id=\"{terminal_id}\") — the live screen\n"
                     f"  job_kill_tool(job_id=\"{job.id}\")   — stop watching (the command "
                     f"keeps running; use terminal_signal_tool to stop it)"),
            data={"job_id": job.id, "terminal_id": terminal_id},
        )


    async def __call__(self, terminal_id: str, text: str = "", submit: bool = True,
                       timeout: Optional[float] = None, run_in_background: bool = False,
                       **kwargs) -> Response:
        terminal, missing = _resolve(terminal_id, _session_of(kwargs))
        if missing is not None:
            return missing

        if text:
            request = PermissionRequest(op=Operation.BASH, target=text)
            allowed = permission_manager.check(self.name, request,
                                               workspace=(config.workspace_root or ""))
            if not allowed.allowed:
                return Response(type=ResponseType.TOOL, success=False,
                                message=f"Permission denied: {allowed.reason}")

        if run_in_background:
            return await self._send_in_background(terminal, terminal_id, text, submit,
                                                  _session_of(kwargs))

        try:
            output, reason = await terminal.send(
                text, submit=submit,
                timeout=float(timeout) if timeout else DEFAULT_SEND_TIMEOUT)
        except TerminalBusy as error:
            return Response(type=ResponseType.TOOL, success=False,
                            message=(f"{error}. Two sends at once interleave into a line "
                                     f"neither of you typed; wait for the first to return."))
        except (RuntimeError, OSError) as error:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Could not send to {terminal_id}: {error}")

        if reason is WaitReason.EXITED:
            header = (f"{terminal_id} — the shell exited (code {terminal.exit_code}). "
                      f"Its output is below; open a new terminal to carry on.")
        elif reason is WaitReason.TIMEOUT:
            header = (f"{terminal_id} — still printing when the wait ran out. It is not "
                      f"lost: the command is still running there. Read it with "
                      f"terminal_read_tool, or send again to wait more.")
        else:
            header = (f"{terminal_id} — the terminal went quiet. That usually means the "
                      f"command finished, but a command that pauses looks the same.")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=_screen_message(header, output or "(nothing new appeared)"),
            data={"terminal_id": terminal_id, "wait_reason": reason.value,
                  "status": terminal.status.value},
        )


@TOOL.register_module(force=True)
class TerminalReadTool(Tool):
    """Read a terminal's screen and scrollback."""

    name: str = "terminal_read_tool"
    description: str = _READ_DESCRIPTION
    instruction: str = _READ_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads retained output; types nothing.")
    mutates: bool = False

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, terminal_id: str, offset: int = 0, count: int = 200,
                       **kwargs) -> Response:
        terminal, missing = _resolve(terminal_id, _session_of(kwargs))
        if missing is not None:
            return missing

        text, total = terminal.read(offset=int(offset), count=int(count))
        header = f"{terminal_id} — {terminal.status.value}, {total} line(s) retained"
        if offset:
            header += f", showing from {offset} line(s) back"
        return Response(
            type=ResponseType.TOOL, success=True,
            message=_screen_message(header, text or "(the terminal has printed nothing)"),
            data={"terminal_id": terminal_id, "total_lines": total,
                  "status": terminal.status.value},
        )


@TOOL.register_module(force=True)
class TerminalListTool(Tool):
    """List this session's terminals."""

    name: str = "terminal_list_tool"
    description: str = _LIST_DESCRIPTION
    instruction: str = _LIST_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads the terminal registry.")
    mutates: bool = False

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, **kwargs) -> Response:
        terminals = terminal_manager.list(_session_of(kwargs))
        if not terminals:
            return Response(type=ResponseType.TOOL, success=True,
                            message="No terminals open in this session.")
        live = sum(1 for t in terminals if not t.status.is_final)
        body = "\n".join(t.summary() for t in terminals)
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{len(terminals)} terminal(s), {live} still alive:\n{body}",
            data={"terminals": [t.snapshot() for t in terminals]},
        )


@TOOL.register_module(force=True)
class TerminalSignalTool(Tool):
    """Signal the command running in a terminal."""

    name: str = "terminal_signal_tool"
    description: str = _SIGNAL_DESCRIPTION
    instruction: str = _SIGNAL_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Signals a process this session started.")
    mutates: bool = True

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, terminal_id: str, signal: str = "SIGINT", **kwargs) -> Response:
        terminal, missing = _resolve(terminal_id, _session_of(kwargs))
        if missing is not None:
            return missing

        try:
            pgid = terminal.send_signal(signal.upper())
        except (ValueError, RuntimeError, OSError) as error:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"Did not signal {terminal_id}: {error}. "
                         f"Allowed signals: {', '.join(ALLOWED_SIGNALS)}."),
            )
        logger.info(f"| 🖥️ {signal} delivered to process group {pgid} of {terminal_id}")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Delivered {signal.upper()} to process group {pgid} in "
                     f"{terminal_id}. The terminal is still open — send to it or read it "
                     f"to see what the signal did."),
            data={"terminal_id": terminal_id, "signal": signal.upper(), "pgid": pgid},
        )


@TOOL.register_module(force=True)
class TerminalCloseTool(Tool):
    """Close a terminal and reap what it was running."""

    name: str = "terminal_close_tool"
    description: str = _CLOSE_DESCRIPTION
    instruction: str = _CLOSE_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Ends a process this session started.")
    mutates: bool = True

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, terminal_id: str, **kwargs) -> Response:
        terminal, missing = _resolve(terminal_id, _session_of(kwargs))
        if missing is not None:
            return missing

        elapsed = terminal.elapsed
        terminal_manager.close(terminal_id)
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Closed {terminal_id} after {elapsed:.1f}s, along with anything it "
                     f"was running. Its state is gone; open a new terminal if you need "
                     f"one."),
            data={"terminal_id": terminal_id, "status": "closed"},
        )
