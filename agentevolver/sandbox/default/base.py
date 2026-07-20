"""OpenSandbox-backed sandbox handle.

Wraps ``opensandbox.Sandbox`` and implements the :class:`~agentevolver.sandbox.types.Sandbox`
contract: shell commands, files, and port exposure. Code execution lives in the
``code_interpreter`` subclass; browser/CDP in the ``playwright`` subclass.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Union

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.process import ensure_server
from agentevolver.sandbox.types import ExecResult, Sandbox, SandboxConfig


def _logs_to_str(logs_field: Any) -> str:
    """opensandbox ExecutionLogs.stdout/stderr may be a str or a list of chunks."""
    if logs_field is None:
        return ""
    if isinstance(logs_field, str):
        return logs_field
    if isinstance(logs_field, (list, tuple)):
        out = []
        for chunk in logs_field:
            out.append(chunk if isinstance(chunk, str) else getattr(chunk, "text", str(chunk)))
        return "".join(out)
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
        tb = getattr(error_obj, "traceback", "") or ""
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

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._started:
            return
        from opensandbox import Sandbox as OSSandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.sandboxes import NetworkPolicy

        await ensure_server(domain=self.config.domain)

        conn = ConnectionConfig(
            domain=self.config.domain,
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
        if not self.config.network:
            # SandboxConfig.network=False -> deny all egress (no default rule matches,
            # so the "deny" default_action applies to everything).
            create_kwargs["network_policy"] = NetworkPolicy(default_action="deny")
        self._sb = await OSSandbox.create(image, **create_kwargs)
        self._started = True
        logger.info(f"| 📦 Sandbox '{self.name}' started (image={image}, network={self.config.network})")

    async def destroy(self) -> None:
        if self._sb is not None:
            try:
                await self._sb.kill()
            except Exception as e:
                logger.warning(f"| ⚠️ Error killing sandbox '{self.name}': {e}")
            finally:
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
    async def write_file(self, path: str, data: Union[str, bytes], *, mode: int = 0o644) -> None:
        sb = self._require()
        await sb.files.write_file(path, data, mode=mode)

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
