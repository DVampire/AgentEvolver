"""A shell that stays where you left it — the environment an agent works in, not a tool.

Every `bash_tool` call is a fresh process, so everything a command changed about its own
environment is gone by the next one: the directory it moved to, the virtualenv it
activated, the host it ssh'd into, the interpreter it started. This holds a real pty open
between calls, and the actions are the ones a person has at a terminal — type at it, look
at it without typing, interrupt what is running, and put it away.

It was six tools, and the shape was wrong in a way that cost the agent a step at a time.
A tool is something you *call*; a terminal is something you are *in*, and the giveaway was
`terminal_read_tool` — a tool whose whole job was to fetch state the agent should already
have been looking at. As an environment, what every open terminal is showing arrives in
`environment-state` each step, refreshed without being asked for, and `read` goes back to
being what it is for: scrollback, and terminals producing output on their own.

The pty, the reader thread and the reaping all stay in `agentevolver.terminal`. Nothing
about the mechanism changed; what changed is who the agent thinks it is talking to.
"""

import asyncio
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.config import config
from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.logger import logger
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import ENVIRONMENT
from agentevolver.terminal import (
    ALLOWED_SIGNALS,
    DEFAULT_SEND_TIMEOUT,
    terminal_manager,
)
from agentevolver.tool.types import clip_output

#: How long a backgrounded send keeps watching. Not "forever": a shell that never goes
#: quiet would hold the waiter for the life of the process, and a job stuck RUNNING is one
#: the agent waits on and nothing ever finishes. An hour is far past any foreground budget
#: while still being a bound.
BACKGROUND_SEND_TIMEOUT = 3600.0

#: How much of one terminal's screen goes into `environment-state`. Every open terminal is
#: rendered every step, so this is paid per terminal per step — enough to see what a
#: command printed, not enough for four terminals to crowd out the conversation. Scrollback
#: is what `read` is for.
STATE_LINES_PER_TERMINAL = 40


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "message": message, **extra}


def _ok(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": True, "message": message, **extra}


@ENVIRONMENT.register_module(force=True)
class TerminalEnvironment(Environment):
    """Terminals that outlive a call, and the actions that drive them."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="terminal")
    description: str = Field(
        default="Terminals that stay open between calls: a shell that keeps its directory, "
                "its virtualenv, its ssh session and its REPL. Open one when the state "
                "between commands is the point; for a single self-contained command, "
                "`bash_tool` is cheaper."
    )
    metadata: Dict[str, Any] = Field(default={"has_vision": False})
    enable_evolving: bool = Field(default=False)

    #: How long a new shell is given to print its first prompt. Waiting for it at open
    #: rather than leaving it to the first send is what keeps a startup banner — or an ssh
    #: password prompt — out of the middle of the first command's output.
    startup_timeout: float = 10.0

    def __init__(self, startup_timeout: float = 10.0, **kwargs: Any):
        super().__init__(startup_timeout=startup_timeout, **kwargs)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _session(ctx) -> str:
        return str(getattr(ctx, "id", "") or "")

    def _resolve(self, terminal_id: str, ctx):
        """The terminal, or a failure naming the ids that do exist.

        A bare "not found" leaves the agent guessing at a handle it is already holding —
        usually one it mistyped, or one belonging to a terminal since closed.
        """
        terminal = terminal_manager.get(terminal_id)
        if terminal is not None:
            return terminal, None
        known = [t.id for t in terminal_manager.list(self._session(ctx))]
        return None, _fail(
            f"No terminal {terminal_id!r}. This session has: "
            f"{', '.join(known) if known else '(none — open one with terminal__open)'}"
        )

    def _permitted(self, command: str) -> Optional[Dict[str, Any]]:
        """Anything typed at a terminal is a command; it goes through the same check."""
        if not command:
            return None
        allowed = permission_manager.check_declared(
            self.name, PermissionRequest(op=Operation.BASH, target=command),
            mode=self.permission_mode,
        )
        return None if allowed.allowed else _fail(f"Permission denied: {allowed.reason}")

    # ------------------------------------------------------------------ actions
    @environment_manager.action(
        name="open",
        permission_op="bash",
        permission_target="command",
        description=(
            "Open a terminal that stays alive between calls, and return its id.\n\n"
            "Unlike `bash_tool`, which runs each command in a new process, everything this "
            "shell does to itself persists: a `cd`, an activated virtualenv, exported "
            "variables, an `ssh` session, a `python`/`psql`/`gdb` prompt you are part-way "
            "through. Use it when the state between commands is the point; for a single "
            "command that stands on its own, `bash_tool` is one call instead of three.\n\n"
            "Give it a `name` you will recognise later — \"build\", \"db\", \"remote\". Pass "
            "`command` to run a program instead of a shell; the terminal ends when that "
            "program does."
        ),
    )
    async def open(self, name: str = "", command: Optional[str] = None,
                   cwd: Optional[str] = None, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        denial = self._permitted(command or "bash -i")
        if denial:
            return denial
        start_in = cwd or config.workspace_root or None
        try:
            terminal = terminal_manager.open(
                name=name, session_id=self._session(ctx), command=command, cwd=start_in)
        except (RuntimeError, ValueError, OSError) as error:
            return _fail(f"Could not open a terminal: {error}")

        # An empty send: type nothing, wait for the shell to settle, take what it printed
        # while starting up.
        opening, _ = await terminal.send("", submit=False, timeout=self.startup_timeout)
        header = (
            f"Opened {terminal.id}" + (f" ({name})" if name else "")
            + f" running {terminal.command or 'bash -i'} in "
              f"{start_in or 'the current directory'}. It stays open until you close it."
        )
        return _ok(f"{header}\n\n{clip_output(opening)}" if opening else header,
                   terminal_id=terminal.id, extra=terminal.snapshot())

    @environment_manager.action(
        name="send",
        permission_op="bash",
        permission_target="text",
        description=(
            "Type text into a terminal, wait for it to go quiet, and return what appeared "
            "because of it — not the whole screen, only what changed.\n\n"
            "READ THE WAIT REASON. A terminal has nothing that says \"the command "
            "finished\": the shell is still there either way, so silence is the only "
            "available evidence.\n"
            "- `idle` — the terminal stopped printing. Usually done; a command that pauses "
            "mid-way looks identical, so if the output stops somewhere implausible, send an "
            "empty text (text: \"\", submit: false) to look again.\n"
            "- `timeout` — still printing when the budget ran out. Nothing is lost: the "
            "command keeps running and the output keeps accumulating, so read it or send "
            "again.\n"
            "- `exited` — the shell itself is gone. Its output is still readable; open a "
            "new terminal.\n\n"
            "`submit` presses Enter (default true); set it false to send control characters "
            "(\"\\u0003\" is ctrl-C) or a line you are still building. Set "
            "`run_in_background` for anything that runs for minutes: it returns at once and "
            "watches the command as a job."
        ),
    )
    async def send(self, terminal_id: str, text: str = "", submit: bool = True,
                   timeout: Optional[float] = None, run_in_background: bool = False,
                   ctx=None, **kwargs: Any) -> Dict[str, Any]:
        terminal, failure = self._resolve(terminal_id, ctx)
        if failure:
            return failure
        denial = self._permitted(text)
        if denial:
            return denial

        if run_in_background:
            return await self._send_in_background(terminal, terminal_id, text, submit, ctx)

        try:
            output, reason = await terminal.send(
                text, submit=submit, timeout=float(timeout or DEFAULT_SEND_TIMEOUT))
        except Exception as error:                                   # noqa: BLE001
            return _fail(f"Could not type into {terminal_id}: {error}")
        label = getattr(reason, "value", reason)
        return _ok(f"[{label}] {clip_output(output)}" if output else f"[{label}] (no output)",
                   terminal_id=terminal_id, wait_reason=str(label))

    async def _send_in_background(self, terminal, terminal_id: str, text: str,
                                  submit: bool, ctx) -> Dict[str, Any]:
        """Type it and return at once, watching the command as a job.

        A command that outlives the turn has no honest foreground answer: the caller either
        waits out the whole budget or reads a completion that has not happened.
        Backgrounding makes the ambiguity explicit — the job is running until it is not,
        and the terminal keeps the output either way.

        Registered in the same registry as a background `bash_tool` command, because
        `job_list_tool` answering "what is outstanding" with only some of the outstanding
        things is worse than not answering.
        """
        from agentevolver.job import job_manager

        job = job_manager.register(type="terminal", label=f"{terminal_id}: {text[:70]}",
                                   session_id=self._session(ctx))
        typed = asyncio.Event()

        async def _wait() -> None:
            # Set before the send, then awaited below. `ensure_future` only *schedules*: a
            # caller that kills in the same turn can cancel this task before it has run a
            # line, and then the command was never typed at all — the job reports killed,
            # the terminal never saw the text, and the two agree on something that did not
            # happen. `send` writes to the pty before its first await, so once this
            # coroutine is running the keystrokes are committed.
            typed.set()
            try:
                output, reason = await terminal.send(
                    text, submit=submit, timeout=BACKGROUND_SEND_TIMEOUT)
                job_manager.append_output(job.id, output or "")
                job_manager.finish(job.id, exit_code=0 if reason is not None else None)
            except Exception as error:                               # noqa: BLE001
                # A waiter that dies silently leaves the job RUNNING for good, and the
                # agent waits on something nothing will ever finish.
                job_manager.finish(job.id, error=f"terminal wait failed: {error}")

        # The task is the handle, so `job_kill_tool` cancels the wait. It does not stop the
        # command — that is `signal`'s job, and conflating them would let "stop watching"
        # read as "stop running".
        job.handle = asyncio.ensure_future(_wait())
        await typed.wait()
        return _ok(
            f"Typed into {terminal_id}; watching it as {job.id}.\n"
            f"  job_output_tool(job_id=\"{job.id}\")  — what it has printed\n"
            f"  terminal__read(terminal_id=\"{terminal_id}\") — the live screen\n"
            f"  job_kill_tool(job_id=\"{job.id}\")   — stop watching (the command keeps "
            f"running; use terminal__signal to stop it)",
            job_id=job.id, terminal_id=terminal_id)

    @environment_manager.action(
        name="read",
        read_only=True,
        description=(
            "Show what a terminal holds — the screen and the lines that have scrolled off "
            "it — without sending anything.\n\n"
            "The current screen of every open terminal already arrives in "
            "`environment-state` each step, so use this for what that cannot show: "
            "scrollback (`offset`/`count` reach back through it), and a terminal producing "
            "output on its own between steps (a build, a server, a watcher)."
        ),
    )
    async def read(self, terminal_id: str, offset: int = 0, count: int = 200,
                   ctx=None, **kwargs: Any) -> Dict[str, Any]:
        terminal, failure = self._resolve(terminal_id, ctx)
        if failure:
            return failure
        body, total = terminal.read(offset=offset, count=count)
        return _ok(clip_output(body) if body else "(nothing yet)",
                   terminal_id=terminal_id, total_lines=total)

    @environment_manager.action(
        name="signal",
        destructive=True,
        description=(
            "Send a signal to the command currently running in a terminal — what ctrl-C "
            "does.\n\n"
            "The signal goes to the running command, not to the shell, so the terminal "
            "survives and keeps everything it was holding. If nothing is running there is "
            "nothing to interrupt: SIGTERM, SIGHUP and SIGKILL are refused in that state "
            "rather than quietly killing the shell. Ending the terminal is `close`, which "
            "is a different intention."
        ),
    )
    async def signal(self, terminal_id: str, signal: str = "SIGINT",
                     ctx=None, **kwargs: Any) -> Dict[str, Any]:
        terminal, failure = self._resolve(terminal_id, ctx)
        if failure:
            return failure
        name = str(signal or "SIGINT").upper()
        if name not in ALLOWED_SIGNALS:
            return _fail(f"{name!r} is not a signal this accepts. "
                         f"Allowed: {', '.join(sorted(ALLOWED_SIGNALS))}")
        try:
            pid = terminal.send_signal(name)
        except Exception as error:                                   # noqa: BLE001
            return _fail(f"Could not signal {terminal_id}: {error}")
        return _ok(f"Sent {name} to the command running in {terminal_id} (pgid {pid}).",
                   terminal_id=terminal_id)

    @environment_manager.action(
        name="close",
        destructive=True,
        description=(
            "End a terminal you are finished with, along with anything it started.\n\n"
            "Its state — the directory, the environment, the REPL — is gone, so close it "
            "when the work that needed that state is done, not between commands. This is "
            "not optional housekeeping: a terminal left open is a live shell for the rest "
            "of the run, and one that is running something is still running it."
        ),
    )
    async def close(self, terminal_id: str, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        terminal, failure = self._resolve(terminal_id, ctx)
        if failure:
            return failure
        closed = terminal_manager.close(terminal_id)
        return _ok(f"Closed {terminal_id}." if closed
                   else f"{terminal_id} had already ended.", terminal_id=terminal_id)

    @environment_manager.action(
        name="list",
        read_only=True,
        description=(
            "Every terminal you opened, oldest first, with its label, whether it is still "
            "alive, and how long it has been there. Use it to recover an id you did not "
            "keep, and before opening another — a terminal you forgot about is still "
            "holding a shell."
        ),
    )
    async def list(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        terminals = terminal_manager.list(self._session(ctx))
        if not terminals:
            return _ok("No terminals open in this session.")
        return _ok("\n".join(t.summary() for t in terminals), count=len(terminals))

    # ------------------------------------------------------------------ state
    async def get_state(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        """What every open terminal is showing, refreshed each step.

        The reason this is an environment rather than six tools. `terminal_read_tool`
        existed because a tool cannot volunteer anything: the agent had to remember to
        look, and an agent that forgets is one typing into a shell it has not seen since
        two commands ago.

        A terminal that *exited on its own* is shown, marked as gone. It is the state
        change most worth volunteering — a build terminal that died between two steps is
        invisible otherwise until something types into it and gets `exited` back, and by
        then the agent has planned a step around a shell that was not there. `close`
        removes a terminal from the registry outright, so one the agent closed on purpose
        never reaches here.
        """
        terminals = terminal_manager.list(self._session(ctx))
        if not terminals:
            return {"success": True, "state": ""}

        blocks: List[str] = []
        for terminal in terminals:
            try:
                # `offset` counts back from the newest line, so 0 is the live tail —
                # which is what a screen is. A negative offset is clamped to 0 by `read`,
                # so it would have worked by accident and read as if it meant something.
                body, _ = terminal.read(offset=0, count=STATE_LINES_PER_TERMINAL)
            except Exception as error:                               # noqa: BLE001
                logger.warning(f"| ⚠️ terminal {terminal.id}: could not read state: {error}")
                body = f"(unreadable — {error})"
            header = terminal.summary()
            if terminal.status.is_final:
                header += " — EXITED; its output is still readable, open a new one to continue"
            blocks.append(f"[{header}]\n{body}" if body
                          else f"[{header}] (nothing printed yet)")
        return {"success": True, "state": "\n\n".join(blocks)}


__all__ = ["TerminalEnvironment"]
