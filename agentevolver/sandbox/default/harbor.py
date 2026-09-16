"""A Harbor-provided environment, wearing this framework's sandbox interface.

Harbor runs the benchmark: it builds the task's container, hands an agent an
``instruction`` and a ``BaseEnvironment``, and afterwards runs the task's own verifier
inside that same container to produce the reward. An agent that wants Harbor's score has
to work in Harbor's environment — provisioning our own would be scoring a different setup
than the one the leaderboard reports, which is the whole reason `deep-swe` 1.1 moved
grading into an isolated container in the first place.

Every tool in this repository already talks to `Sandbox` and nothing below it, so making
Harbor's environment *be* a `Sandbox` is what lets `bash_tool`, `apply_patch_tool` and the
rest run against a Harbor task without one of them changing. The two interfaces line up
almost exactly, and both sides are async, so this is a translation and not a bridge:
`exec` → `run_command`, and Harbor's native file transfer replaces the base class's
base64-through-the-shell defaults.

What this deliberately does NOT do is manage a lifecycle. Harbor starts the container
before the agent is called and stops it after the verifier has run, so `start` and
`destroy` here are no-ops: tearing down a container mid-task would take the verifier's
subject with it.
"""

from datetime import timedelta
from typing import Any, Dict, Optional, Union

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.types import ExecResult, Sandbox, SandboxConfig


@SANDBOX.register_module(name="harbor", force=True)
class HarborSandbox(Sandbox):
    """Runs commands in the container Harbor built for one benchmark task."""

    name: str = "harbor"
    description: str = "A Harbor task environment, so this framework's tools can run inside it."

    def __init__(self, config: Optional[SandboxConfig] = None, *, environment: Any = None,
                 **kwargs: Any):
        super().__init__(config, **kwargs)
        if environment is None:
            raise ValueError(
                "HarborSandbox wraps an environment Harbor already built; construct it with "
                "`HarborSandbox(environment=<BaseEnvironment>)` from inside a Harbor agent."
            )
        self._environment = environment

    @property
    def environment(self) -> Any:
        """The wrapped Harbor environment, for the few places that need Harbor's own API."""
        return self._environment

    @property
    def container_workspace(self) -> Optional[str]:
        """Unknown to this backend, deliberately.

        Harbor resolves a task's working directory itself — every one of its environments
        does so privately (`_detect_workdir`, `_effective_cwd`, `_resolve_workdir`) and
        none exposes the answer, because `task.toml`'s `workdir` only *overrides* the
        container's own WORKDIR when it is set. There is nothing to read, so naming a
        path here would be a guess: an earlier version answered `/app`, which is where
        SWE-bench Pro's images keep their tree and is wrong for any task built otherwise.

        The base contract reads None as "host paths, no remapping". That is not quite
        this case — the container is real — but it is the honest answer to "which path",
        and the alternative was a directory that may not exist.
        """
        return None

    @property
    def resource_id(self) -> Optional[str]:
        """Harbor owns this container's identity; nothing here may outlive its trial."""
        return None

    async def start(self) -> None:
        """No-op: Harbor started this container before it called the agent."""

    async def destroy(self) -> None:
        """No-op: Harbor stops the container after its verifier has run.

        Destroying it here would delete the very filesystem the reward is computed from.
        """

    async def is_alive(self) -> bool:
        try:
            result = await self._environment.exec("true")
        except Exception:
            return False
        return getattr(result, "return_code", 1) == 0

    async def run_command(
        self,
        command: str,
        *,
        workspace_root: Optional[str] = None,
        timeout: Optional[Union[int, timedelta]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecResult:
        seconds = int(timeout.total_seconds()) if isinstance(timeout, timedelta) else timeout
        try:
            result = await self._environment.exec(
                command,
                # Only when the caller named one. Harbor applies the task's own working
                # directory when cwd is absent, and substituting a default here would
                # run every command somewhere the task never chose.
                cwd=workspace_root or None,
                env=env or None,
                timeout_sec=seconds,
            )
        except Exception as exc:
            # A raised exec is still a command result to the caller: tools read
            # ExecResult, and letting this propagate would end the trial on a failure the
            # agent could have read and worked around.
            logger.warning(f"| ⚓ harbor exec failed: {type(exc).__name__}: {exc}")
            return ExecResult(success=False, error=f"{type(exc).__name__}: {exc}", exit_code=None)

        code = getattr(result, "return_code", 0)
        return ExecResult(
            success=code == 0,
            stdout=getattr(result, "stdout", "") or "",
            stderr=getattr(result, "stderr", "") or "",
            exit_code=code,
        )

    async def write_file(self, path: str, data: Union[str, bytes], *, mode: int = 0o644) -> None:
        """Use Harbor's own transfer rather than the base class's base64-through-a-shell.

        The default works everywhere but pays a shell round trip per file and inherits
        every quoting hazard of arbitrary content; Harbor already has a file channel into
        this container.
        """
        import os
        import tempfile

        raw = data.encode("utf-8") if isinstance(data, str) else data
        handle, staged = tempfile.mkstemp()
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(raw)
            await self._environment.upload_file(staged, path)
            await self.run_command(f"chmod {mode:o} {path!r}")
        finally:
            os.unlink(staged)

    async def read_bytes(self, path: str) -> bytes:
        import os
        import tempfile

        handle, staged = tempfile.mkstemp()
        os.close(handle)
        try:
            await self._environment.download_file(path, staged)
            with open(staged, "rb") as fh:
                return fh.read()
        finally:
            if os.path.exists(staged):
                os.unlink(staged)

    async def expose_port(self, port: int) -> str:
        """Harbor's task containers are graded offline; nothing outside may reach in."""
        raise NotImplementedError(
            "A Harbor task container is not addressable from outside its trial."
        )
