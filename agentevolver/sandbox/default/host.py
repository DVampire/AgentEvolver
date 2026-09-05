"""HostSandbox — a NON-isolated "sandbox" that runs directly on the host.

This is the fallback backend for when no container runtime (Docker daemon or k8s)
is available, so deployments still work on a plain machine. It implements the same
:class:`~agentevolver.sandbox.types.Sandbox` surface the deployment manager uses
(``run_command`` / ``write_file`` / ``expose_port``) but against the host filesystem
and host processes — there is **no isolation**. A background start command (one ending
in ``&``) is launched in its own process group and tracked so ``destroy`` can kill it.

Use only for dev/demo or trusted single-tenant hosts. For real isolation use the
``opensandbox`` backend (which needs Docker/k8s).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from datetime import timedelta
from typing import Any, Dict, List, Optional, Union

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.types import ExecResult, Sandbox, SandboxConfig
from agentevolver.sandbox.process import owned_command


@SANDBOX.register_module(name="host", force=True)
class HostSandbox(Sandbox):
    """Run commands/servers directly on the host (no container isolation)."""

    name: str = "host"
    description: str = "Runs services directly on the host — fallback when no container runtime is available (NO isolation)."

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        super().__init__(config, **kwargs)
        self._procs: List[subprocess.Popen] = []   # tracked background processes
        self._root: str = ""

    @staticmethod
    def _process_start_ticks(pid: int) -> Optional[int]:
        try:
            # Split after the final ')' because a process name may contain spaces.
            fields = open(f"/proc/{pid}/stat", encoding="utf-8").read().rsplit(") ", 1)[1].split()
            return int(fields[19])  # proc stat field 22; fields starts at field 3
        except (OSError, ValueError, IndexError):
            return None

    @property
    def resource_id(self) -> Optional[str]:
        for process in reversed(self._procs):
            if process.poll() is not None:
                continue
            start = self._process_start_ticks(process.pid)
            if start is not None:
                return f"{process.pid}:{start}:{os.getpgid(process.pid)}"
        return None

    @staticmethod
    def _group_alive(group: int) -> Optional[bool]:
        """Zombies cannot serve requests, even when their parent has not reaped them."""
        try:
            for name in os.listdir("/proc"):
                if not name.isdecimal():
                    continue
                try:
                    with open(f"/proc/{name}/stat", encoding="utf-8") as handle:
                        fields = handle.read().rsplit(") ", 1)[1].split()
                    if int(fields[2]) == group and fields[0] not in {"Z", "X"}:
                        return True
                except FileNotFoundError:
                    continue  # Process exited while enumerating.
            return False
        except (OSError, ValueError, IndexError):
            return None  # Cannot verify; fail closed.

    @classmethod
    async def destroy_resource(cls, resource_id: str) -> bool:
        """Stop a persisted host group only while its leader identity still matches."""
        try:
            pid_text, start_text, group_text = resource_id.split(":", 2)
            pid, start, group = int(pid_text), int(start_text), int(group_text)
        except (AttributeError, TypeError, ValueError):
            return False
        if pid <= 1 or group <= 1 or cls._process_start_ticks(pid) != start:
            return False
        try:
            if os.getpgid(pid) != group:
                return False
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            if cls._group_alive(group) is False:
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, os.WNOHANG)
                return True
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return True
            await asyncio.sleep(0.05)
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            if cls._group_alive(group) is False:
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, os.WNOHANG)
                return True
            await asyncio.sleep(0.05)
        return cls._group_alive(group) is False

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._started:
            return
        base = getattr(self.config, "host_base", None) or os.path.join("workspace_root", "deploy_host")
        self._root = os.path.abspath(base)
        os.makedirs(self._root, exist_ok=True)
        self._started = True
        logger.info(f"| 🖥️  HostSandbox started (root={self._root}, NO isolation)")

    async def destroy(self) -> None:
        processes = list(self._procs)
        for process in processes:
            try:
                group = os.getpgid(process.pid)
                os.killpg(group, signal.SIGTERM)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        deadline = asyncio.get_running_loop().time() + 3.0
        while any(process.poll() is None for process in processes):
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.05)
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                group = os.getpgid(process.pid)
                os.killpg(group, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        for process in processes:
            try:
                await asyncio.to_thread(process.wait, timeout=1.0)
            except Exception:
                pass
        survivors = [process.pid for process in processes if process.poll() is None]
        self._procs.clear()
        self._started = False
        if survivors:
            raise RuntimeError(
                f"could not verify shutdown of host process groups: {survivors}"
            )

    # ------------------------------------------------------------- execution
    async def run_command(
        self,
        command: str,
        *,
        workspace_root: Optional[str] = None,
        timeout: Optional[Union[int, timedelta]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecResult:
        cwd = workspace_root or self._root
        try:
            os.makedirs(cwd, exist_ok=True)
        except Exception:
            pass
        run_env = {**os.environ, **(self.config.env or {}), **(env or {})}

        stripped = command.rstrip()
        if stripped.endswith("&"):
            # Background launch (a server): run it in its own session so the whole
            # process group can be killed later; return immediately.
            bg = stripped[:-1].strip()
            try:
                p = subprocess.Popen(
                    owned_command(["/bin/sh", "-c", bg]), cwd=cwd, env=run_env,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._procs.append(p)
                return ExecResult(success=True, stdout=f"launched pid {p.pid}")
            except Exception as e:
                return ExecResult(success=False, error=f"failed to launch: {e}")

        secs = timeout.total_seconds() if isinstance(timeout, timedelta) else timeout
        try:
            r = subprocess.run(
                owned_command(["/bin/sh", "-c", command]), cwd=cwd, env=run_env,
                capture_output=True, text=True, timeout=secs,
            )
            return ExecResult(
                success=(r.returncode == 0), stdout=r.stdout or "",
                stderr=r.stderr or "", exit_code=r.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(success=False, error=f"command timed out after {secs}s")
        except Exception as e:
            return ExecResult(success=False, error=f"command failed: {e}")

    # ------------------------------------------------------------- files
    # Deliberately NOT sandbox.types.DEFAULT_FILE_MODE (0o777). That default is
    # justified by the target being a disposable container the framework owns, where
    # the container is the isolation boundary. This backend writes real host files with
    # no isolation at all (see the class description), possibly on a shared machine, so
    # it keeps the conventional mode.
    async def write_file(self, path: str, data: Union[str, bytes], *, mode: int = 0o644) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb" if isinstance(data, (bytes, bytearray)) else "w") as fh:
            fh.write(data)
        try:
            os.chmod(path, mode)
        except Exception:
            pass

    async def read_file(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    async def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    def launched_alive(self) -> bool:
        """True if a background server was launched and is still running.

        Returns True when nothing has been launched yet (nothing to judge), and False
        only when we started a server that has since exited (e.g. failed to bind a port)
        — the deployment manager uses this so a dead server is not mistaken for "up".
        """
        if not self._procs:
            return True
        return any(p.poll() is None for p in self._procs)

    # ------------------------------------------------------------- network
    async def expose_port(self, port: int) -> str:
        # The server binds a host port directly; it is reachable at localhost on this host.
        return f"http://localhost:{port}"
