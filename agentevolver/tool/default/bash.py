"""Bash tool for executing shell commands."""
import asyncio
import os
import signal
import sys
from typing import Any, Dict, Optional

from pydantic import Field

from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.config import config
from agentevolver.tool.types import OUTPUT_LIMIT, Tool, clip_output
from agentevolver.response.types import Response, ResponseType

_DESCRIPTION = "Execute bash commands in the shell."

_INSTRUCTION = """
## Function
Execute bash commands in the shell.

## Guidance
- Use this tool to run system commands, scripts, or any bash operations.
- Be careful with commands that modify the system or require elevated privileges.
- For file operations, ALWAYS use ABSOLUTE paths to avoid path-related issues.
- Input should be a VALID bash command string.
- The command's exit code is reported in the output. A non-zero exit code is an
  observation, not a tool error (e.g. `grep` returns 1 when it finds no matches);
  read STDOUT/STDERR and the exit code to decide whether the command did what you
  intended.
- One call can carry several steps, and doing so costs one model round-trip instead
  of several: `make && ./run-tests`, `a; b; echo $?`, pipelines,
  a heredoc that writes a file and then runs it. Use `&&` when a later step is
  pointless if an earlier one fails, `;` when you want every step to run regardless.
  Only chain steps whose commands you already know — if the next command depends on
  output you have not read yet, that is a separate call.

- Some programs behave differently, or refuse to run at all, when their output is not a
  terminal — anything that draws a screen, prompts, pages, or colourises. `tty: true`
  gives the command a real terminal, and `stdin` sends it keystrokes. Without those, such
  a program is not merely inconvenient to test: its entire behaviour is invisible, and
  runs both fail identically, which reads as agreement.

## Parameters
- command (str): The command to execute. If file path is necessary, it should be an absolute path.
- tty (bool, optional): Run under a pseudo-terminal. Needed for anything that requires a
  terminal to work — full-screen programs, REPLs, prompts. You get back what the terminal
  displays — the screen, plus a note of the colours and boldness used — not the escape
  sequences that produced it. stdout and stderr are one stream, as on a real terminal.
  TERM is set for you when the environment has none; prefix the command with `TERM=vt100 `
  and such to choose one, since behaviour under different terminal types is itself worth
  comparing. Default false.
- stdin (str, optional): Text fed to the command's input. With `tty: true` these are
  keystrokes, so a program can be driven and then told to quit.
- timeout (int, optional): Seconds before the command is abandoned. Keep it small for
  anything that may not exit on its own.

## Example
{"name": "bash_tool", "args": {"command": "ls -l /path/to/file.txt"}}
{"name": "bash_tool", "args": {"command": "./viewer --colour red", "tty": true, "stdin": "q", "timeout": 3}}
"""


#: Terminal size reported to a program running under `tty: true`. A full-screen program
#: asks for this and lays itself out accordingly; zero would make many of them misbehave
#: or refuse to start, which would look like a defect in the program rather than in how it
#: was run.
_PTY_ROWS, _PTY_COLS = 24, 80

#: TERM given to a `tty: true` command when the environment does not set one. A terminal
#: device alone is not enough for anything built on curses: it also looks TERM up in the
#: terminfo database, and with no TERM it reports "Error opening terminal: unknown" and
#: exits — the same refusal as having no terminal at all, which is the thing `tty` exists
#: to get past. `xterm` because it is present in essentially every image.
#:
#: Overridable, and deliberately: how a program behaves under a different TERM — `dumb`,
#: `vt100`, one that does not exist — is itself a behaviour worth comparing. Prefix the
#: command with `TERM=… ` to choose.
_PTY_DEFAULT_TERM = "xterm"


def _render_terminal(data: bytes) -> str:
    """Interpret a pty byte stream the way a terminal would and return what it displays.

    A terminal is not a pipe with a flag set: the bytes a full-screen program writes are
    instructions to a device — move here, set this colour, erase to end of line — and the
    thing a person sees is the screen those instructions leave behind. Handing the raw
    stream to a caller hands over the wire protocol instead of the page. Measured on one
    task: a screen holding roughly 500 visible characters arrived as 32,184 bytes of escape
    sequences, which exhausted the output budget and was skimmed past as noise.

    Colour and boldness survive as a summary line rather than being dropped, because for a
    program whose whole job is how it draws, "it is green and bold" is the observation.
    """
    import pyte

    screen = pyte.HistoryScreen(_PTY_COLS, _PTY_ROWS, history=200)
    stream = pyte.ByteStream(screen)
    stream.feed(data)

    def row_text(row) -> str:
        return "".join(row[x].data for x in range(_PTY_COLS)).rstrip()

    lines = [row_text(row) for row in screen.history.top]
    lines += [line.rstrip() for line in screen.display]
    while lines and not lines[-1]:
        lines.pop()

    # Distinct styles in first-seen order — a handful is descriptive, a hundred is noise.
    styles: list = []
    for row in list(screen.history.top) + [screen.buffer[y] for y in range(_PTY_ROWS)]:
        for x in range(_PTY_COLS):
            char = row[x]
            if not char.data.strip():
                continue
            name = char.fg if char.fg != "default" else ""
            if char.bg != "default":
                name = f"{name or 'default'} on {char.bg}"
            if char.bold:
                name = f"{name or 'default'} bold"
            if name and name not in styles:
                styles.append(name)

    note = f"[terminal {_PTY_COLS}x{_PTY_ROWS}"
    if styles:
        shown = ", ".join(styles[:6]) + (", …" if len(styles) > 6 else "")
        note += f" · {shown}"
    note += f" · {len(data):,} bytes interpreted]"
    return "\n".join(lines + ["", note])


def _run_under_pty(command: str, cwd, env, timeout: float, stdin: str) -> tuple:
    """Run `command` attached to a pseudo-terminal. Returns (output, exit_code, timed_out).

    Blocking, so callers hand it to an executor.

    A program that checks whether it is talking to a terminal takes a different path when
    it is not — drawing nothing, refusing to start, dropping colour, skipping a prompt.
    Comparing two such programs without a terminal compares two refusals, and they agree.
    That is not a hypothetical: an entire class of a reference's behaviour was invisible
    this way, and the reconstruction that never implemented it looked correct.

    stdout and stderr are one stream here, as they are on a real terminal; a caller that
    needs them apart should run without a tty.
    """
    import fcntl
    import pty
    import select
    import struct
    import subprocess
    import termios
    import time

    if not env.get("TERM"):
        env = {**env, "TERM": _PTY_DEFAULT_TERM}

    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", _PTY_ROWS, _PTY_COLS, 0, 0))
    except OSError:
        pass  # a size we could not set is not worth failing over

    process = subprocess.Popen(
        command, shell=True, stdin=slave, stdout=slave, stderr=slave,
        cwd=cwd, env=env, start_new_session=True, close_fds=True,
    )
    os.close(slave)

    chunks: list = []
    total = 0
    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        if stdin:
            os.write(master, stdin.encode("utf-8", "replace"))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master], [], [], min(remaining, 0.5))
            if not ready:
                if process.poll() is not None:
                    break        # exited and drained
                continue
            try:
                data = os.read(master, 65536)
            except OSError:
                break            # EIO: the child closed the terminal, i.e. it exited
            if not data:
                break
            # Bounded here as well as at the end: a program that redraws a screen can
            # produce megabytes per second, and holding all of it to clip later is how a
            # 777MB capture file happened.
            total += len(data)
            if total <= OUTPUT_LIMIT * 4:
                chunks.append(data)
    finally:
        if process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        process.wait()
        os.close(master)

    raw = b"".join(chunks)
    try:
        output = _render_terminal(raw)
    except Exception as error:  # an emulator that chokes must not lose the observation
        output = raw.decode("utf-8", errors="replace") + f"\n[terminal not rendered: {error}]"
    if total > len(raw):
        output += f"\n[... {total - len(raw):,} further bytes were produced and not shown ...]"
    return output, process.returncode, timed_out


@TOOL.register_module(force=True)
class BashTool(Tool):
    """A tool for executing bash commands asynchronously."""

    name: str = "bash_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    progress_policy: str = "workspace"
    timeout: int = Field(default=600, description="Timeout in seconds for command execution")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(
        self,
        command: str,
        tty: bool = False,
        stdin: str = "",
        timeout: Optional[int] = None,
        **kwargs,
    ) -> Response:
        """Execute a bash command asynchronously.

        Args:
            command: The shell command to run.
            tty:     Attach a pseudo-terminal. Programs that draw a screen, prompt, or
                     colourise take a different path — often refusing to run — when their
                     output is not a terminal, so without this their behaviour cannot be
                     observed at all.
            stdin:   Text fed to the command. Keystrokes, when `tty` is set.
            timeout: Seconds before the command is abandoned; the tool's own default
                     otherwise.
        """
        limit = int(timeout) if timeout else self.timeout
        if not command.strip():
            return Response(type=ResponseType.TOOL, success=False, message="Error: Empty command provided")

        ctx = kwargs.get("ctx")

        # Permission check
        req = PermissionRequest(op=Operation.BASH, target=command)
        result = permission_manager.check(
            self.name, req, workspace=(config.workspace_root or "")
        )
        if not result.allowed:
            return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")

        warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""

        try:
            # Commands run in the current runtime environment, which is the container the
            # agent system is running inside. Keep python3 and pip on the interpreter that
            # launched us.
            runtime_bin = os.path.dirname(sys.executable)
            command_env = {
                **os.environ,
                "PATH": runtime_bin + os.pathsep + os.environ.get("PATH", ""),
            }
            # Every session command runs from its isolated workspace.  Besides
            # keeping relative outputs contained, this makes ordinary scripts
            # (``open('results/x.json', 'w')``) behave consistently with the
            # workspace path shown to the agent.
            workspace_root = config.workspace_root
            cwd = os.path.abspath(workspace_root) if workspace_root else None
            if cwd and not os.path.isdir(cwd):
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=f"Workspace directory does not exist: {cwd}",
                )
            if tty:
                loop = asyncio.get_running_loop()
                output, exit_code, timed_out = await loop.run_in_executor(
                    None, _run_under_pty, command, cwd, command_env, float(limit), stdin,
                )
                if timed_out:
                    return Response(
                        type=ResponseType.TOOL, success=False,
                        message=(
                            f"Error: Command timed out after {limit} seconds under a "
                            f"terminal and was abandoned. A program that holds the "
                            f"terminal will not exit on its own — send it whatever key "
                            f"quits it via `stdin`, or wrap it: `timeout 2 <command>`. "
                            f"Partial output:\n{clip_output(output)}"
                        ),
                        data={"exit_code": None, "command": command, "timed_out": True, "tty": True},
                    )
                message = warning_prefix + (clip_output(output) or
                                            f"Command completed with exit code: {exit_code}")
                if exit_code:
                    message = f"{message}\n\nExit code: {exit_code}"
                return Response(
                    type=ResponseType.TOOL, success=True, message=message,
                    data={"exit_code": exit_code, "command": command, "tty": True},
                )

            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE if stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=command_env,
                cwd=cwd,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(stdin.encode("utf-8") if stdin else None),
                    timeout=limit,
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                # Name the cause and what to do about it. A timeout the agent cannot act
                # on gets repeated: observed when `./executable -z`, on a reconstruction
                # that fell through into its TUI loop, blocked for the full timeout and
                # the agent learned nothing from it. Programs that do not exit on their
                # own — a TUI, a REPL, a server, a watcher — are common enough that the
                # remedy belongs in the message rather than in a doc somewhere.
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message=(
                        f"Error: Command timed out after {limit} seconds and was "
                        f"abandoned. If the program you invoked can run without exiting — "
                        f"a TUI, a REPL, a server, or a loop — wrap it: `timeout 2 "
                        f"<command>` returns exit code 124 instead of blocking. "
                        f"Command: {command}"
                    ),
                    data={"exit_code": None, "command": command, "timed_out": True},
                )

            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

            # Clipped per stream: a command that floods stdout must not also cost the
            # agent the stderr that explains why.
            parts = []
            if stdout_str:
                parts.append(f"STDOUT:\n{clip_output(stdout_str)}")
            if stderr_str:
                parts.append(f"STDERR:\n{clip_output(stderr_str)}")

            exit_code = process.returncode
            if exit_code != 0:
                parts.append(f"Exit code: {exit_code}")

            message = warning_prefix + ("\n\n".join(parts) if parts else f"Command completed with exit code: {exit_code}")

            # The bash *tool call* succeeds whenever the command actually ran to
            # completion — the shell exit code is an observation for the model to read
            # (it is included in the message and in `data["exit_code"]`), not a tool
            # malfunction. Treating every non-zero exit as a hard failure mislabels
            # ordinary diagnostics — `grep -c` returns 1 on zero matches, `ls missing`
            # returns 2 — as "❌ Action failed", which floods the logs and can mislead
            # the model into thinking its own deliverables broke. Genuine command
            # failures stay fully visible via STDERR and the exit code; only the tool
            # itself failing (timeout, spawn error, empty command) is success=False.
            return Response(type=ResponseType.TOOL,
                success=True,
                message=message,
                data={"exit_code": exit_code, "command": command},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error executing command: {e}")
