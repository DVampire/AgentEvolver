"""The SSH transport behind the remote-host environment.

Split from ``environment.py`` the way ``browser/service.py`` is: this file knows how to
talk to a machine, and nothing about the action surface the model sees.

Everything runs over one **multiplexed** connection. OpenSSH's ControlMaster opens the
TCP connection and authenticates once; every later command rides that socket and costs a
round trip rather than a handshake. Without it each command re-authenticates — a few
hundred milliseconds each, which across the dozens of commands a single agent turn issues
is the difference between usable and not.

The master is per *session*, so two conversations never share a channel, and closing one
does not disturb the other.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agentevolver.logger import logger

#: Where the multiplexing sockets live. Kept out of the output tree deliberately: a socket
#: is a live kernel object, not a run artifact, and a stray one in a results directory is
#: confusing at best. The path also has to stay short — see `_socket_path`.
_CONTROL_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "agentevolver-ssh"

#: How long the master survives with no channels on it. Long enough that a pause in the
#: conversation does not pay for a fresh handshake, short enough that an abandoned session
#: does not hold a connection open all day.
_CONTROL_PERSIST = "10m"

#: A unix socket path is capped at ~104 bytes on macOS and 108 on Linux, and the limit is
#: on the *whole* path. `ssh` fails with a bare "unix_listener: path too long" that says
#: nothing about which path, so the name is kept short rather than descriptive.
_SOCKET_NAME_LEN = 12

#: How long a command will queue for the shared shell before opening its own channel.
#: The shell is one serial stream, so concurrent commands must take turns — but a caller
#: should not wait behind a slow one indefinitely when a fresh channel is available. The
#: number is the measured cost of that alternative: wait about as long as going around
#: would cost, then go around.
_SHELL_WAIT_SECONDS = 1.2


@dataclass
class SSHResult:
    """One command's outcome. `screen` is set only when a pty was requested."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    screen: Optional[str] = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class SSHConfig:
    host: str
    user: str = ""
    port: int = 22
    identity_file: str = ""
    jump_host: str = ""
    #: Every command is run from here, and every path the environment accepts is resolved
    #: against it. See `SSHService.resolve` for why that is a boundary and not a default.
    workspace_root: str = "~"
    connect_timeout: int = 15
    known_hosts_strict: bool = True
    extra_options: Dict[str, str] = field(default_factory=dict)

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


class RemotePathError(ValueError):
    """A path that resolved outside the workspace root."""


class SSHService:
    """One multiplexed SSH connection and the commands run over it."""

    def __init__(self, config: SSHConfig, session_key: str):
        self._config = config
        self._session_key = session_key
        self._socket: Optional[Path] = None
        self._started = False
        self._resolved_root: Optional[str] = None
        self._shell: Optional[asyncio.subprocess.Process] = None
        #: One shell, one stream. Two coroutines writing commands into it and
        #: reading replies back interleave their output and asyncio refuses the
        #: second reader outright — `read() called while another coroutine is
        #: already waiting`. An agent that batches actions in a step does exactly
        #: that, so turns are taken explicitly.
        self._shell_lock = asyncio.Lock()

    # ------------------------------------------------------------------ plumbing
    def _socket_path(self) -> Path:
        import hashlib

        digest = hashlib.sha256(
            f"{self._config.target}:{self._config.port}:{self._session_key}".encode()
        ).hexdigest()[:_SOCKET_NAME_LEN]
        return _CONTROL_DIR / digest

    def _base_args(self) -> List[str]:
        args = ["ssh", "-o", f"ControlPath={self._socket}"]
        if self._config.port != 22:
            args += ["-p", str(self._config.port)]
        if self._config.identity_file:
            args += ["-i", os.path.expanduser(self._config.identity_file)]
        if self._config.jump_host:
            args += ["-J", self._config.jump_host]
        if not self._config.known_hosts_strict:
            # Off by default. Accepting an unknown key silently is how a man in the middle
            # goes unnoticed, and this connection carries the agent's whole reach.
            args += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        for key, value in self._config.extra_options.items():
            args += ["-o", f"{key}={value}"]
        return args

    async def _exec(self, argv: List[str], *, timeout: float, stdin: bytes = b"") -> Tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin or None), timeout=timeout)
            return proc.returncode or 0, out, err
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Open the multiplexing master, then resolve the workspace root once."""
        if self._started:
            return
        _CONTROL_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._socket = self._socket_path()

        if not await self.is_alive():
            argv = self._base_args() + [
                "-M", "-N", "-f",
                "-o", "ControlMaster=yes",
                "-o", f"ControlPersist={_CONTROL_PERSIST}",
                "-o", f"ConnectTimeout={self._config.connect_timeout}",
                "-o", "BatchMode=yes",
                self._config.target,
            ]
            code, _out, err = await self._exec(argv, timeout=self._config.connect_timeout + 10)
            if code != 0:
                raise ConnectionError(
                    f"ssh to {self._config.target} failed: {err.decode(errors='replace').strip()[:300]}"
                )

        # Resolved once and cached: `~` and symlinks mean the configured root and the path
        # a command actually lands in can differ, and the boundary check compares real
        # paths. Doing it per call would add a round trip to every action.
        #
        # `~` is expanded here rather than quoted through. shlex.quote turns `~/proj` into
        # `'~/proj'`, which the remote shell treats as a literal — the first version of
        # this created a directory *named* `~` in the user's home and resolved the
        # workspace to `/home/user/~/proj`. Only a leading `~/` is special; anything else
        # is quoted as normal.
        raw_root = self._config.workspace_root
        if raw_root == "~" or raw_root.startswith("~/"):
            quoted_root = '"$HOME"' + shlex.quote(raw_root[1:]) if len(raw_root) > 1 else '"$HOME"'
        else:
            quoted_root = shlex.quote(raw_root)
        code, out, err = await self._exec(
            self._base_args() + [self._config.target,
                                 f"mkdir -p {quoted_root} && cd {quoted_root} && pwd -P"],
            timeout=30,
        )
        if code != 0:
            raise ConnectionError(
                f"workspace_root {self._config.workspace_root!r} unusable on {self._config.target}: "
                f"{err.decode(errors='replace').strip()[:200]}"
            )
        self._resolved_root = out.decode().strip()
        self._started = True
        logger.info(
            f"| 🔌 SSH master up: {self._config.target} (workspace={self._resolved_root}, "
            f"persist={_CONTROL_PERSIST})"
        )

    async def is_alive(self) -> bool:
        if self._socket is None:
            self._socket = self._socket_path()
        try:
            code, _out, _err = await self._exec(
                self._base_args() + ["-O", "check", self._config.target], timeout=10
            )
            return code == 0
        except (asyncio.TimeoutError, OSError):
            return False

    async def stop(self) -> None:
        await self._discard_shell()
        if self._socket is None:
            return
        try:
            await self._exec(self._base_args() + ["-O", "exit", self._config.target], timeout=10)
        except (asyncio.TimeoutError, OSError):
            pass
        self._started = False
        logger.info(f"| ⚫ SSH master closed: {self._config.target}")

    # ------------------------------------------------------------------ paths
    @property
    def target(self) -> str:
        """`user@host` for this connection — what a log line should name."""
        return self._config.target

    @property
    def workspace_root(self) -> str:
        return self._resolved_root or self._config.workspace_root

    def resolve(self, path: str) -> str:
        """Resolve `path` against the workspace root, refusing anything that escapes it.

        The boundary is the whole safety story for a remote host. Read/write permission
        says *whether* the agent may write; this says *where*, and it is the one that keeps
        a mistake inside a project directory instead of loose on a shared machine. Local
        tools get the same guarantee from `check_session_path`; there is no equivalent for
        a path on another host, so it lives here.

        Resolution is lexical on purpose. Asking the remote to `realpath` would be more
        faithful to symlinks but costs a round trip per path, and a lexical check that
        refuses `..` outright cannot be walked out of by any sequence of segments.
        """
        candidate = (path or "").strip()
        if not candidate:
            return self.workspace_root
        candidate = candidate.replace("\\", "/")
        if candidate.startswith("~"):
            raise RemotePathError(
                f"path {path!r} starts with ~: give a path relative to the workspace "
                f"({self.workspace_root}) or an absolute path inside it"
            )
        root = PurePosixPathish(self.workspace_root)
        target = root.join(candidate) if not candidate.startswith("/") else PurePosixPathish(candidate)
        normalised = target.normalise()
        if not normalised.is_within(root):
            raise RemotePathError(
                f"path {path!r} resolves to {normalised} which is outside the workspace "
                f"root {self.workspace_root}"
            )
        return str(normalised)

    # ------------------------------------------------------------------ execution
    async def run(
        self,
        command: str,
        *,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        tty: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> SSHResult:
        """Run one command on the far end, from the workspace root unless told otherwise."""
        directory = self.resolve(cwd) if cwd else self.workspace_root
        prefix = "".join(f"export {k}={shlex.quote(v)}; " for k, v in (env or {}).items())
        remote = f"cd {shlex.quote(directory)} && {prefix}{command}"

        argv = self._base_args()
        if tty:
            # -tt forces a pty even though our own stdin is not one. Without it a
            # full-screen program on the far end sees a pipe and either refuses to draw or
            # emits nothing worth rendering, which is exactly the case `tty` is for.
            argv += ["-tt"]
        argv += [self._config.target, remote]

        if not tty:
            # Fast path first; it returns None when the shared shell cannot be trusted,
            # and the slow path below is always correct.
            fast = await self._run_in_shell(remote, timeout)
            if fast is not None:
                return fast

        try:
            code, out, err = await self._exec(argv, timeout=timeout)
        except asyncio.TimeoutError:
            return SSHResult(
                exit_code=124,
                timed_out=True,
                stderr=(
                    f"timed out after {timeout:.0f}s. The remote process may still be "
                    f"running — this only gave up waiting. Use `launch` for anything "
                    f"long-running so it survives and can be followed."
                ),
            )

        if tty:
            from agentevolver.utils.terminal import render_terminal

            # Same renderer the local shell tool uses. A remote pty emits the same device
            # instructions a local one does, so there is one implementation of "what the
            # screen showed" and both sides benefit when it improves.
            return SSHResult(exit_code=code, screen=render_terminal(out + err))

        return SSHResult(
            exit_code=code,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )

    # ------------------------------------------------------- persistent shell
    #
    # Multiplexing removes the *handshake*, not the per-command cost. Every command still
    # opens a session channel, and the server sets one up — fork, PAM session, environment
    # — before anything runs. Measured against hpc-boan-gpu2, where the network RTT is
    # 0.39ms: `ssh -O check` (no session channel) returns in 0.01s, while running
    # `/bin/true` over an established master takes 1.21s. All of it is the channel.
    #
    # Keeping one channel open with a shell on the far end and feeding it commands drops
    # that to 0.0003s — measured, same host, ~4000x. The cost is fragility: one shell
    # serving every command means `exit`, a segfault in a sourced script, or a command that
    # closes stdin takes the whole session down. So the fast path is guarded and always
    # falls back to a fresh channel, which is slow but cannot be broken by anything the
    # previous command did.
    _SENTINEL_PREFIX = "__AE_RC_"

    async def _ensure_shell(self) -> bool:
        if self._shell is not None and self._shell.returncode is None:
            return True
        self._shell = None
        try:
            self._shell = await asyncio.create_subprocess_exec(
                *self._base_args(), self._config.target,
                "bash --norc --noprofile -s",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:  # noqa: BLE001 — the slow path still works
            logger.warning(f"| ⚠️ persistent shell unavailable on {self._config.target}: {exc}")
            return False

        # Drain whatever the client and the login path emit before the first command —
        # ssh's own warnings ("Identity file ... not accessible"), a MOTD, an rc file that
        # prints. None of it belongs to a command, but it arrives on the same stream, and
        # without this it is returned as the first action's output.
        await self._prime_shell()
        return True

    async def _prime_shell(self) -> None:
        import uuid as _uuid

        shell = self._shell
        if shell is None or shell.stdin is None or shell.stdout is None:
            return
        marker = f"{self._SENTINEL_PREFIX}PRIME_{_uuid.uuid4().hex[:8]}".encode()
        try:
            shell.stdin.write(b"printf '\n" + marker + b"\n'\n")
            await shell.stdin.drain()
            buffer = b""
            while marker not in buffer:
                chunk = await asyncio.wait_for(shell.stdout.read(65536), timeout=20)
                if not chunk:
                    self._shell = None
                    return
                buffer += chunk
        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            await self._discard_shell()

    async def _run_in_shell(self, remote: str, timeout: float) -> Optional[SSHResult]:
        """Run in the shared shell. Returns None when the fast path cannot be trusted."""
        try:
            await asyncio.wait_for(self._shell_lock.acquire(), timeout=_SHELL_WAIT_SECONDS)
        except asyncio.TimeoutError:
            # Someone else is mid-command on the shared stream and is taking longer than
            # a fresh channel would cost. Returning None sends this caller down the
            # fallback path rather than making it wait.
            return None
        try:
            return await self._run_in_shell_locked(remote, timeout)
        finally:
            self._shell_lock.release()

    async def _run_in_shell_locked(self, remote: str, timeout: float) -> Optional[SSHResult]:
        if not await self._ensure_shell():
            return None
        import uuid as _uuid

        token = _uuid.uuid4().hex[:12]
        sentinel = f"{self._SENTINEL_PREFIX}{token}_"
        shell = self._shell
        assert shell is not None and shell.stdin is not None and shell.stdout is not None

        # The command runs in a subshell so a `cd` or a variable it sets cannot leak into
        # the next one — each action should start from the same place regardless of what
        # ran before it.
        payload = f"( {remote} )\nprintf '\\n{sentinel}%d\\n' $?\n"
        try:
            shell.stdin.write(payload.encode())
            await shell.stdin.drain()
        except (ConnectionResetError, BrokenPipeError):
            self._shell = None
            return None

        buffer = b""
        marker = sentinel.encode()
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while marker not in buffer:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                chunk = await asyncio.wait_for(shell.stdout.read(65536), timeout=remaining)
                if not chunk:
                    # The shell died on us — most likely the command was `exit`, or it
                    # closed stdin. Whatever it was, this channel is unusable now.
                    self._shell = None
                    return None
                buffer += chunk
        except asyncio.TimeoutError:
            # A hung command owns the shared shell forever, so the shell is discarded
            # rather than left in an unknown state with output still to come.
            await self._discard_shell()
            return SSHResult(
                exit_code=124,
                timed_out=True,
                stderr=(
                    f"timed out after {timeout:.0f}s. The remote process may still be "
                    f"running — this only gave up waiting. Use `launch` for anything "
                    f"long-running so it survives and can be followed."
                ),
            )

        body, _, tail = buffer.rpartition(marker)
        try:
            code = int(tail.split()[0])
        except (IndexError, ValueError):
            return None
        # The sentinel is printed after a leading newline so it always starts a line, even
        # when the command's output did not end in one. That newline is ours, not the
        # command's, and leaving it in appends a blank line to every single result.
        text = body.decode(errors="replace")
        if text.endswith("\n"):
            text = text[:-1]
        return SSHResult(exit_code=code, stdout=text)

    async def _discard_shell(self) -> None:
        shell, self._shell = self._shell, None
        if shell is None or shell.returncode is not None:
            return
        try:
            shell.kill()
            await shell.wait()
        except (ProcessLookupError, OSError):
            pass

    async def run_raw(self, command: str, *, timeout: float = 60.0) -> SSHResult:
        """Run without the workspace `cd` — for probes that describe the machine itself."""
        argv = self._base_args() + [self._config.target, command]
        try:
            code, out, err = await self._exec(argv, timeout=timeout)
        except asyncio.TimeoutError:
            return SSHResult(exit_code=124, timed_out=True)
        return SSHResult(
            exit_code=code,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )

    # ------------------------------------------------------------------ transfer
    async def rsync(self, source: str, destination: str, *, timeout: float = 1800.0) -> SSHResult:
        """Copy with rsync over the same multiplexed connection.

        rsync rather than scp: it resumes, it is incremental, and it handles directories
        without a separate flag dance. `-e` hands it the same ControlPath, so a transfer
        rides the existing connection instead of authenticating again.
        """
        ssh_cmd = " ".join(shlex.quote(part) for part in self._base_args())
        argv = ["rsync", "-az", "--partial", "-e", ssh_cmd, source, destination]
        try:
            code, out, err = await self._exec(argv, timeout=timeout)
        except asyncio.TimeoutError:
            return SSHResult(exit_code=124, timed_out=True, stderr=f"rsync timed out after {timeout:.0f}s")
        return SSHResult(
            exit_code=code,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )

    def remote_spec(self, path: str) -> str:
        """`user@host:/resolved/path`, the form rsync and scp want."""
        return f"{self._config.target}:{self.resolve(path)}"


class PurePosixPathish:
    """Just enough POSIX path algebra to enforce the boundary, without touching the disk.

    `pathlib.PurePosixPath` would do this, except that its `relative_to` raises rather
    than answering a question, and `..` is not collapsed — so `a/../../b` compares as
    being under `a`. Normalising first and comparing segment lists is the check that
    actually holds.
    """

    def __init__(self, raw: str):
        self.raw = raw

    def join(self, other: str) -> "PurePosixPathish":
        return PurePosixPathish(f"{self.raw.rstrip('/')}/{other.lstrip('/')}")

    def normalise(self) -> "PurePosixPathish":
        parts: List[str] = []
        for segment in self.raw.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(segment)
        return PurePosixPathish("/" + "/".join(parts))

    def is_within(self, root: "PurePosixPathish") -> bool:
        mine = self.normalise().raw.split("/")
        theirs = root.normalise().raw.split("/")
        return mine[: len(theirs)] == theirs

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"PurePosixPathish({self.raw!r})"
