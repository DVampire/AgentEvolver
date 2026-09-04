"""Bash tool for executing shell commands."""
import asyncio
import datetime as _dt
import os
import re
import secrets
import signal
import sys
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.paths import P, path_manager
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import OUTPUT_LIMIT, Tool
from agentevolver.utils.terminal import (
    PTY_COLS,
    PTY_DEFAULT_TERM,
    PTY_KEYSTROKE_DELAY,
    PTY_ROWS,
    render_terminal,
)

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
- Every command's COMPLETE output is archived to a `.txt` under the session's
  `log/bash/` directory, and the tool result tells you the path. What you are shown
  inline may be an excerpt for a very long command; when you need the full transcript,
  read that file (`cat`/`grep` it) rather than re-running the command. A background job's
  archive is named by its job id, so it pairs with `job__output(job_id=...)`.
- One call can carry several known execution steps, and doing so costs one model round-trip
  instead of several: `make && ./run-tests`, `a; b; echo $?`, or a pipeline. Use
  `apply_patch_tool` for authored source/configuration changes when it is mounted; do not
  hide a large hand-written file inside a shell heredoc. Use `&&` when a later step is
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

# Benchmark launchers can keep the Agent runtime on the host while commands execute in
# the task image. This avoids importing a host-built Python/Conda runtime into arbitrary
# glibc and musl images; only the shell crosses that boundary.
_EXEC_CONTAINER_ENV = "AGENTEVOLVER_EXEC_CONTAINER"
_EXEC_WORKDIR_ENV = "AGENTEVOLVER_EXEC_WORKDIR"
_DOCKER = os.environ.get("AGENTEVOLVER_DOCKER", "docker")


async def _run_in_container(
    container: str,
    workdir: str,
    command: str,
    stdin: str,
    timeout: float,
) -> tuple[str, str, Optional[int], bool]:
    """Run one shell command in a launcher-owned task container.

    Docker receives every control value as an argv item; only ``command`` is interpreted,
    and it is interpreted by the shell inside the task container. ``bash -c`` deliberately
    avoids a login profile replacing the PATH baked into the image.
    """
    args = [_DOCKER, "exec"]
    if stdin:
        args.append("-i")
    if workdir:
        args.extend(("--workdir", workdir))
    args.extend((container, "bash", "-c", command))
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin.encode("utf-8") if stdin else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return "", "", None, True
    return (
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
        process.returncode,
        False,
    )


def _bash_archive_path(stem: Optional[str] = None) -> Optional[str]:
    """Create (if needed) the session's bash-log directory and return a `.txt` path
    inside it — or ``None`` if the directory cannot be resolved or made.

    ``stem`` names the file when the command has a handle the agent refers to it by —
    a background job's id, so ``job__output(job_id="job_ab12cd34")`` and the file
    ``job_ab12cd34.txt`` are visibly the same run, the way Claude Code names a task's
    output file by the task id. A foreground call has no such handle, so it falls back
    to a timestamp plus a short random token: the directory then lists in the order
    commands ran, and two calls in the same microsecond still get separate files.
    """
    try:
        directory = path_manager.get(P.SESSION_BASH)
        directory.mkdir(parents=True, exist_ok=True)
        if stem is None:
            stem = f"{_dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{secrets.token_hex(3)}"
        return str(directory / f"{stem}.txt")
    except Exception:                                          # noqa: BLE001
        return None


def _write_bash_archive(command: str, text: str, path: Optional[str] = None) -> Optional[str]:
    """Archive one command's complete output to the session's bash log; return the path.

    Archiving never fails the command: a command that ran is a command that ran, and
    losing the ability to file its transcript must not be reported as the command
    breaking (same rule the oversized-result spill follows). The tool result the model
    reads is still bounded by the universal output policy; this is the durable, complete
    copy beside it — written here for a foreground call, appended live by the background
    drain — so nothing a command printed is lost, including the head a long-running job's
    bounded in-memory buffer would otherwise drop.
    """
    if not text and path is None:
        return None
    try:
        path = path or _bash_archive_path()
        if path is None:
            return None
        header = f"$ {command}\n{'-' * 60}\n"
        # 'x' (O_EXCL) rather than 'w': a pre-existing path — a planted symlink or the
        # vanishingly unlikely token collision — is an error, never a redirect.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(header + (text or ""))
        return path
    except Exception:                                          # noqa: BLE001
        return None


def _with_archive_note(message: str, archived: Optional[str]) -> str:
    """Append the archive locator to a tool message, when the output was archived."""
    if not archived:
        return message
    return f"{message}\n\n[📄 full output archived at {archived}]"


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

        # Archive the full transcript to disk as it streams, named by the job id so the
        # file and `job__output(job_id=...)` are visibly the same run. The registry keeps
        # only a bounded tail in memory (it drops the head of a chatty job), so the file
        # is the one complete record of what a long-running command printed.
        archive_path = _write_bash_archive(command, "", path=_bash_archive_path(stem=job.id))

        async def _drain() -> None:
            loop = _asyncio.get_running_loop()
            handle = None
            if archive_path:
                try:
                    handle = open(archive_path, "a", encoding="utf-8")
                except Exception:                                  # noqa: BLE001
                    handle = None
            try:
                while True:
                    line = await loop.run_in_executor(None, process.stdout.readline)
                    if not line:
                        break
                    job_manager.append_output(job.id, line)
                    if handle is not None:
                        try:
                            handle.write(line)
                            handle.flush()
                        except Exception:                          # noqa: BLE001
                            handle = None
                code = await loop.run_in_executor(None, process.wait)
                job_manager.finish(job.id, exit_code=code)
            except Exception as error:                              # noqa: BLE001
                # A reader that dies silently leaves the job RUNNING forever, and the
                # agent waits on something nothing will ever finish.
                job_manager.finish(job.id, error=f"output reader failed: {error}")
            finally:
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:                              # noqa: BLE001
                        pass

        _asyncio.ensure_future(_drain())
        archive_line = (f"\n  full output is also archived at {archive_path}"
                        if archive_path else "")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(
                f"{warning_prefix}Started in the background as {job.id}.\n"
                f"It is running now; keep working and collect it when you need the "
                f"result:\n"
                f"  job__output(job_id=\"{job.id}\")  — what it has printed so far\n"
                f"  job__list()                      — every job and its state\n"
                f"  job__kill(job_id=\"{job.id}\")    — stop it"
                f"{archive_line}"
            ),
            data={"job_id": job.id, "status": job.status.value, "archived": archive_path},
        )

    def permission_request(self, arguments, ctx=None):
            """Classify the exact shell string before the execution body is entered."""
            return PermissionRequest(
                op=Operation.BASH, target=str(arguments.get("command") or "")
            )

    def will_mutate(self, arguments: Dict[str, Any]) -> Optional[bool]:
        """Recognise ordinary inspection commands for the no-progress guard."""
        command = str(arguments.get("command") or "").strip()
        if not command:
            return False
        # Ignore stderr/stdout disposal; other redirection writes a file.
        effect_text = re.sub(r"\b[012]?>\s*/dev/null\b", "", command)
        if re.search(
            r"(?:^|[;&|]\s*)(?:rm|mv|cp|mkdir|touch|install|patch|tee)\b|"
            r"\bsed\s+(?:-[A-Za-z]*i\b|--in-place\b)|"
            r"\b(?:git\s+)?(?:add|commit|apply|reset|checkout)\b|"
            r"(?<![012])>{1,2}",
            effect_text,
            re.IGNORECASE,
        ):
            return True

        read_only = {
            "cat", "cd", "cut", "diff", "du", "env", "find", "git", "grep",
            "head", "ls", "pwd", "rg", "sed", "sort", "stat", "tail", "test",
            "true", "uniq", "wc", "which",
        }
        segments = [part.strip() for part in re.split(r"&&|\|\||[;|]", effect_text)]
        for segment in segments:
            if not segment:
                continue
            words = segment.split()
            while words and ("=" in words[0] or words[0] in {"command", "env", "sudo"}):
                words.pop(0)
            if not words or os.path.basename(words[0]) not in read_only:
                return None
            if words[0] == "git" and len(words) > 1 and words[1] not in {
                "branch", "diff", "log", "rev-parse", "show", "status",
            }:
                return None
        return False

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
        from agentevolver.session import isolated_workspace_root, resolve_workspace_root
        result = permission_manager.check_declared(
            self.name, req, mode=self.permission_mode,
            workspace=isolated_workspace_root(ctx),
        )
        if not result.allowed:
            return Response(type=ResponseType.TOOL, success=False, message=f"Permission denied: {result.reason}")

        warning_prefix = f"Warning: {result.warning}\n\n" if result.warning else ""

        try:
            exec_container = os.environ.get(_EXEC_CONTAINER_ENV, "").strip()
            if exec_container:
                if run_in_background or tty:
                    mode = "background" if run_in_background else "TTY"
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=(
                            f"Error: {mode} execution is unavailable for this externally "
                            "controlled task container; run a bounded foreground command."
                        ),
                    )
                stdout_str, stderr_str, exit_code, timed_out = await _run_in_container(
                    exec_container,
                    os.environ.get(_EXEC_WORKDIR_ENV, "/workspace"),
                    command,
                    stdin,
                    float(limit),
                )
                if timed_out:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"Error: Command timed out after {limit} seconds and was abandoned. Command: {command}",
                        data={"exit_code": None, "command": command, "timed_out": True},
                    )
                parts = []
                if stdout_str:
                    parts.append(f"STDOUT:\n{stdout_str}")
                if stderr_str:
                    parts.append(f"STDERR:\n{stderr_str}")
                if exit_code:
                    parts.append(f"Exit code: {exit_code}")
                body = "\n\n".join(parts) if parts else f"Command completed with exit code: {exit_code}"
                archived = _write_bash_archive(command, body)
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=_with_archive_note(warning_prefix + body, archived),
                    data={"exit_code": exit_code, "command": command, "archived": archived},
                )

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
            workspace_root = resolve_workspace_root(ctx)
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
                body = output or f"Command completed with exit code: {exit_code}"
                if exit_code:
                    body = f"{body}\n\nExit code: {exit_code}"
                archived = _write_bash_archive(command, body)
                message = _with_archive_note(warning_prefix + body, archived)
                return Response(
                    type=ResponseType.TOOL, success=True, message=message,
                    data={"exit_code": exit_code, "command": command, "tty": True,
                          "archived": archived},
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

            body = "\n\n".join(parts) if parts else f"Command completed with exit code: {exit_code}"
            archived = _write_bash_archive(command, body)
            message = _with_archive_note(warning_prefix + body, archived)

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
                data={"exit_code": exit_code, "command": command, "archived": archived},
            )

        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=f"Error executing command: {e}")
