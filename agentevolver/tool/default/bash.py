"""Bash tool for executing shell commands."""
import asyncio
import os
import signal
import sys
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.config import config
from agentevolver.tool.types import OUTPUT_LIMIT, Tool
from agentevolver.utils.terminal import (
    PTY_COLS,
    PTY_DEFAULT_TERM,
    PTY_KEYSTROKE_DELAY,
    PTY_ROWS,
    render_terminal,
)
from agentevolver.response.types import Response, ResponseType

_DESCRIPTION = "Execute bash commands in the shell."

_GUIDANCE = """
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
"""

_EXAMPLES = [
    '{"name": "bash_tool", "args": {"command": "ls -l /path/to/file.txt"}}',
    '{"name": "bash_tool", "args": {"command": "./viewer --colour red", "tty": true, "stdin": "q", "timeout": 3}}',
    '{"name": "bash_tool", "args": {"command": "python train.py", "run_in_background": true}}',
]


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
        env = {**env, "TERM": PTY_DEFAULT_TERM}

    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", PTY_ROWS, PTY_COLS, 0, 0))
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
    started = time.monotonic()
    deadline = started + timeout
    send_keys_at = started + min(PTY_KEYSTROKE_DELAY, timeout / 2) if stdin else None
    try:
        while True:
            if send_keys_at is not None and time.monotonic() >= send_keys_at:
                os.write(master, stdin.encode("utf-8", "replace"))
                send_keys_at = None
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
        output = render_terminal(raw)
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
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    timeout: int = Field(default=600, description="Timeout in seconds for command execution")
    #: Deliberately above `timeout`: the command budget is what should fire, so the tool
    #: gets to return its own diagnostic — which command, how long, what partial output —
    #: instead of the pipeline cutting the call off with a message naming neither.
    call_timeout_seconds: float = 660

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def _start_background(self, command, cwd, env, ctx, warning_prefix) -> Response:
        """Start the command, register it, and return its handle without waiting.

        Output is drained by a reader task rather than left in the pipe. A pipe holds
        about 64 KB before the writer blocks, so a chatty command left undrained does not
        merely lose output — it stops making progress, and looks to the agent exactly like
        a slow one. The registry is the only place output accumulates.
        """
        import asyncio as _asyncio
        import subprocess

        from agentevolver.job import job_manager

        process = subprocess.Popen(
            command, shell=True, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # Its own process group, so `job__kill` can signal the whole tree. A
            # shell command is usually a shell that spawned the real work; signalling
            # only the leader leaves that work running while the registry calls it dead.
            start_new_session=True, close_fds=True, text=True, bufsize=1,
        )
        job = job_manager.register(
            type="bash", label=command,
            session_id=str(getattr(ctx, "id", "") or ""), handle=process,
        )

        async def _drain() -> None:
            loop = _asyncio.get_running_loop()
            try:
                while True:
                    line = await loop.run_in_executor(None, process.stdout.readline)
                    if not line:
                        break
                    job_manager.append_output(job.id, line)
                code = await loop.run_in_executor(None, process.wait)
                job_manager.finish(job.id, exit_code=code)
            except Exception as error:                              # noqa: BLE001
                # A reader that dies silently leaves the job RUNNING forever, and the
                # agent waits on something nothing will ever finish.
                job_manager.finish(job.id, error=f"output reader failed: {error}")

        _asyncio.ensure_future(_drain())
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(
                f"{warning_prefix}Started in the background as {job.id}.\n"
                f"It is running now; keep working and collect it when you need the "
                f"result:\n"
                f"  job__output(job_id=\"{job.id}\")  — what it has printed so far\n"
                f"  job__list()                      — every job and its state\n"
                f"  job__kill(job_id=\"{job.id}\")    — stop it"
            ),
            data={"job_id": job.id, "status": job.status.value},
        )

    def permission_request(self, arguments, ctx=None):
            """Classify the exact shell string before the execution body is entered."""
            return PermissionRequest(
                op=Operation.BASH, target=str(arguments.get("command") or "")
            )

    async def __call__(
        self,
        command: str,
        tty: bool = False,
        stdin: str = "",
        timeout: Optional[int] = None,
        run_in_background: bool = False,
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
            run_in_background: Return a job id at once instead of waiting. The command
                     outlives the call and is collected through the `job_*` tools.
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
            if run_in_background:
                if tty:
                    return Response(
                        type=ResponseType.TOOL, success=False,
                        message=(
                            "Error: `tty` and `run_in_background` cannot be combined. A "
                            "terminal exists to be interacted with, and a backgrounded "
                            "one has nobody to type at it — the program would draw its "
                            "screen and wait forever. Run it in the foreground with a "
                            "small `timeout`, or drop `tty` and background it."
                        ),
                    )
                return await self._start_background(command, cwd, command_env, ctx,
                                                    warning_prefix)

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
                            f"Partial output:\n{output}"
                        ),
                        data={"exit_code": None, "command": command, "timed_out": True, "tty": True},
                    )
                message = warning_prefix + (output or
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
                parts.append(f"STDOUT:\n{stdout_str}")
            if stderr_str:
                parts.append(f"STDERR:\n{stderr_str}")

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
