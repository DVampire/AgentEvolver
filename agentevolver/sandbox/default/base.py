"""OpenSandbox-backed sandbox handle.

Wraps ``opensandbox.Sandbox`` and implements the :class:`~agentevolver.sandbox.types.Sandbox`
contract: shell commands, files, and port exposure. Code execution lives in the
``playwright`` subclass for browser/CDP.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Dict, List, Optional, Union

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.process import ensure_server
from agentevolver.sandbox.types import DEFAULT_FILE_MODE, ExecResult, Sandbox, SandboxConfig

#: Where scripts/run-in-sandbox.sh bind-mounts the repo inside the container.
CONTAINER_REPO_ROOT = "/AgentEvolver"


def to_host_path(path: str) -> str:
    """Translate an in-container path to the host path a bind mount needs.

    The opensandbox daemon reaches the HOST Docker daemon over a mounted socket,
    so bind-mount sources are resolved in the host's mount namespace — not ours.
    When the framework is itself running inside the repo container, a path like
    ``/AgentEvolver/output/...`` does not exist on the host and Docker would
    silently create an empty directory there instead of sharing the real one.

    ``scripts/run-in-sandbox.sh`` exports ``AGENTEVOLVER_HOST_ROOT`` with the
    host path it mounted at ``/AgentEvolver``; that mapping cannot be derived
    reliably from /proc/self/mountinfo, which reports the source relative to its
    filesystem root rather than the host's mount namespace. Unset (running
    directly on the host) means paths are already host paths.
    """
    host_root = os.environ.get("AGENTEVOLVER_HOST_ROOT")
    if not host_root:
        return path
    if path == CONTAINER_REPO_ROOT:
        return host_root
    if path.startswith(f"{CONTAINER_REPO_ROOT}/"):
        return host_root.rstrip("/") + path[len(CONTAINER_REPO_ROOT):]
    return path


def _logs_to_str(logs_field: Any, *, sep: str = "\n") -> str:
    """opensandbox ExecutionLogs.stdout/stderr may be a str or a list of chunks.

    The chunks are **one per output line, with the trailing newline stripped**, so
    they must be rejoined with "\n". Verified against a live sandbox:

        printf 'A\nB\nC\n'                -> ['A', 'B', 'C']
        printf 'X\n\nY\n'                 -> ['X', '\n', 'Y']
        printf '  leading spaces\nx\n'    -> ['  leading spaces', 'x']

    An empty line arrives as a chunk whose text is exactly "\n", so each chunk is
    rstripped of newlines before joining — otherwise a blank line would double.
    Leading whitespace is preserved by the API and must not be touched.

    This defaulted to sep="" on the assumption that these were raw stream chunks
    that concatenate without an implied break. They are not, and the cost was
    severe: every command run through a sandbox came back with its line structure
    destroyed — `ls -la` as one run-together line, and a program's `--help` output
    collapsed from fifteen lines to one. Any agent comparing two programs' output
    line by line was doing so on text whose lines had been glued together.

    The final trailing newline of a stream cannot be recovered (the API does not
    distinguish "C\n" from "C" in the last chunk); that is harmless for comparison
    because it is lost identically on both sides.
    """
    if logs_field is None:
        return ""
    if isinstance(logs_field, str):
        return logs_field
    if isinstance(logs_field, (list, tuple)):
        out = []
        for chunk in logs_field:
            text = chunk if isinstance(chunk, str) else getattr(chunk, "text", str(chunk))
            out.append(text.rstrip("\n") if isinstance(text, str) else str(text))
        return sep.join(out)
    return str(logs_field)


def execution_to_result(execution: Any) -> ExecResult:
    """Normalize an opensandbox ``Execution`` into an :class:`ExecResult`."""
    logs = getattr(execution, "logs", None)
    stdout = _logs_to_str(getattr(logs, "stdout", "")) if logs else ""
    stderr = _logs_to_str(getattr(logs, "stderr", "")) if logs else ""
    exit_code = getattr(execution, "exit_code", None)

    results: List[str] = []
    for r in getattr(execution, "result", None) or []:
        text = getattr(r, "text", None)
        if text is not None:
            results.append(text)

    error_obj = getattr(execution, "error", None)
    error: Optional[str] = None
    if error_obj is not None:
        name = getattr(error_obj, "name", "") or ""
        value = getattr(error_obj, "value", "") or ""
        # traceback is documented/typed as a string but opensandbox actually returns a
        # list of message lines for at least CommandExecError (e.g. ["exit status 2"]) —
        # an unconditional "\n".join([..., tb]) TypeErrors on every non-zero exit
        # otherwise: "sequence item 1: expected str instance, list found", confirmed
        # live — this silently broke every failed command run through this sandbox
        # backend before.
        tb = _logs_to_str(getattr(error_obj, "traceback", "") or "", sep="\n")
        error = "\n".join(p for p in [f"{name}: {value}".strip(": "), tb] if p).strip() or str(error_obj)

    success = error is None and (exit_code in (None, 0))
    return ExecResult(
        success=success, stdout=stdout, stderr=stderr,
        exit_code=exit_code, results=results, error=error,
    )


@SANDBOX.register_module(name="opensandbox", force=True)
class OpenSandbox(Sandbox):
    """Generic opensandbox container: shell commands + files + port exposure."""

    name: str = "opensandbox"
    description: str = "Generic OpenSandbox container (shell, files, ports)."
    default_image: str = "opensandbox/base:latest"
    default_entrypoint: Optional[List[str]] = None

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        super().__init__(config, **kwargs)
        self._sb = None  # opensandbox.Sandbox instance
        self._sandbox_id: Optional[str] = None  # ledger entry for crash-safe reaping

    @property
    def container_workspace(self) -> Optional[str]:
        # opensandbox mounts the session workspace (and the ProgramBench task image's
        # WORKDIR) at /workspace; bash_tool runs there, so the prompt must say /workspace.
        return "/workspace"

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._started:
            return
        from opensandbox import Sandbox as OSSandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.sandboxes import NetworkPolicy, Volume, Host

        # Resolve once so the daemon and the client connect to the same domain
        # (config.domain is None by default -> port-manager-assigned port).
        from agentevolver.sandbox.process import default_domain
        domain = self.config.domain or default_domain()
        await ensure_server(domain=domain)

        conn = ConnectionConfig(
            domain=domain,
            api_key=self.config.api_key,
            request_timeout=timedelta(seconds=60),
        )
        image = self.config.image or self.default_image
        entrypoint = self.config.entrypoint or self.default_entrypoint
        create_kwargs: Dict[str, Any] = dict(
            timeout=timedelta(minutes=self.config.timeout_minutes),
            env=self.config.env or None,
            connection_config=conn,
        )
        if entrypoint:
            create_kwargs["entrypoint"] = entrypoint
        # Bind-mount host dirs (e.g. the repo at /AgentEvolver) so files stay consistent
        # between the base container and this peer. Sources are rewritten to host
        # paths because the daemon binds them in the host's mount namespace.
        if self.config.mounts:
            create_kwargs["volumes"] = [
                Volume(name=f"mount{i}", host=Host(path=to_host_path(host_path)), mount_path=container_path)
                for i, (host_path, container_path) in enumerate(self.config.mounts.items())
            ]
        if not self.config.network:
            # SandboxConfig.network=False -> deny all egress (no default rule matches,
            # so the "deny" default_action applies to everything).
            create_kwargs["network_policy"] = NetworkPolicy(default_action="deny")
        self._sb = await OSSandbox.create(image, **create_kwargs)
        self._started = True
        # Record the container in the crash ledger so an unclean process exit
        # cannot leak it: the next boot's reap_stale() removes whatever a dead
        # run left behind (see agentevolver/sandbox/ledger.py).
        try:
            from agentevolver.sandbox.ledger import ledger
            info = await self._sb.get_info()
            self._sandbox_id = str(getattr(info, "id", None) or getattr(info, "sandbox_id", "") or "") or None
            if self._sandbox_id:
                ledger.record(self._sandbox_id)
        except Exception as e:  # noqa: BLE001 — the sandbox works even if untracked
            logger.warning(f"| ⚠️ Sandbox '{self.name}': could not record in the crash ledger: {e}")
        logger.info(f"| 📦 Sandbox '{self.name}' started (image={image}, network={self.config.network})")

    async def destroy(self) -> None:
        if self._sb is not None:
            try:
                await self._sb.kill()
            except Exception as e:
                logger.warning(f"| ⚠️ Error killing sandbox '{self.name}': {e}")
            finally:
                if self._sandbox_id:
                    try:
                        from agentevolver.sandbox.ledger import ledger
                        ledger.forget(self._sandbox_id)
                    except Exception:  # noqa: BLE001
                        pass
                    self._sandbox_id = None
                self._sb = None
                self._started = False

    def _require(self):
        if self._sb is None:
            raise RuntimeError(f"Sandbox '{self.name}' not started; call await start() first.")
        return self._sb

    # ------------------------------------------------------------- execution
    async def run_command(
        self,
        command: str,
        *,
        workspace_root: Optional[str] = None,
        timeout: Optional[Union[int, timedelta]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecResult:
        from opensandbox.models.execd import RunCommandOpts

        sb = self._require()
        to = timedelta(seconds=timeout) if isinstance(timeout, int) else timeout
        opts = RunCommandOpts(working_directory=workspace_root, timeout=to, envs=env or None)
        try:
            execution = await sb.commands.run(command, opts=opts)
            return execution_to_result(execution)
        except Exception as e:
            return ExecResult(success=False, error=f"command failed: {e}")

    # ------------------------------------------------------------- files
    async def write_file(self, path: str, data: Union[str, bytes], *, mode: int = DEFAULT_FILE_MODE) -> None:
        """Write a file inside the container. `mode` is a normal Python octal int.

        opensandbox's `mode` is not the integer it appears to be: the server renders
        whatever int it receives in decimal and then parses *that string* as base 8
        (`strconv.ParseUint(s, 8, ...)`). Passing a genuine `0o644` therefore sent
        "420", which it read as 0o420 — `-r---w----`, a file its own owner cannot
        write and a different user cannot read at all. `0o755` was worse: it sent
        "493" and the call failed outright with
        `[RUNTIME_ERROR] error chmoding file ... parsing "493": invalid syntax`.

        This was not cosmetic. Every file an agent wrote through write_file_tool or
        edit_file_tool landed as 0o420, the mode survives the submission tarball
        (verified across a tar round-trip), and the ProgramBench task images default
        to the non-root user `agent` — so a graded build would have failed to read the
        very source it was asked to compile. Confirmed live: `su agent -c 'cat <file>'`
        returned "Permission denied".

        `f"{mode:o}"` renders the octal *digits*, which is what the server actually
        wants, so callers keep the conventional 0o644/0o755 spelling.
        """
        sb = self._require()
        await sb.files.write_file(path, data, mode=int(f"{mode:o}"))

    async def read_file(self, path: str) -> str:
        sb = self._require()
        return await sb.files.read_file(path)

    async def read_bytes(self, path: str) -> bytes:
        sb = self._require()
        return await sb.files.read_bytes(path)

    # ------------------------------------------------------------- network
    async def expose_port(self, port: int) -> str:
        sb = self._require()
        endpoint = await sb.get_endpoint(port)
        host = getattr(endpoint, "endpoint", str(endpoint))
        return f"http://{host}" if not str(host).startswith("http") else str(host)
