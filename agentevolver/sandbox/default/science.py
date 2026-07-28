"""JupyterLab on GPUs, in a container this class launches itself.

One container per gateway session, the same shape as the Code view's VS Code
container — but this one talks to the Docker daemon directly instead of going
through opensandbox.

That is not a stylistic choice. opensandbox's ``[docker]`` configuration has no
device option, so a container it starts cannot be given GPUs, and the science
workstation exists to train models. Everything opensandbox was doing for the
other sandboxes (start, publish a port, reap) is small enough to do here.

Exposes one published port:
  8888  JupyterLab — HTTP *and* its WebSocket, same port (``lab_url``)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.default.base import to_host_path
from agentevolver.sandbox.types import ExecResult, Sandbox, SandboxConfig

#: Fixed inside the container, published on an ephemeral loopback port.
LAB_PORT = 8888

_IMAGE = "agentevolver/science:latest"
# repo root: .../agentevolver/sandbox/default/science.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCKERFILE = "docker/science/Dockerfile"


@SANDBOX.register_module(name="science", force=True)
class ScienceSandbox(Sandbox):
    """JupyterLab serving one session workspace, with the host's GPUs attached."""

    name: str = "science"
    description: str = "JupyterLab workstation with CUDA GPUs, editing the session workspace."
    default_image: str = _IMAGE

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)
        self._container: Optional[Any] = None
        self._host_port: Optional[int] = None

    # ----------------------------------------------------------------- client
    @staticmethod
    def _client():
        """The Docker daemon, over the mounted socket.

        The SDK rather than the ``docker`` CLI because the framework normally
        runs *inside* the base container, which mounts the socket but ships no
        CLI — shelling out worked on a developer's host and nowhere else.
        """
        import docker  # noqa: PLC0415 — optional dependency, part of the [sandbox] extra

        return docker.from_env()

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Build the image if needed, then run the container and find its port."""
        if self._container is not None:
            return
        await asyncio.to_thread(self._start_blocking)

    def _start_blocking(self) -> None:
        client = self._client()
        image = self.config.image or self.default_image
        self._ensure_image(client, image)

        kwargs: Dict[str, Any] = {
            "image": image,
            "detach": True,
            "auto_remove": True,
            # Bound to loopback on an ephemeral port: the Lab is reached only
            # through the gateway's authorised proxy route, never from off-host.
            # The framework's own container runs with --network host, so this
            # loopback address is the same one it can connect to.
            "ports": {f"{LAB_PORT}/tcp": ("127.0.0.1", None)},
            "environment": dict(self.config.env or {}),
            # Bind sources resolve in the HOST's mount namespace, not ours: the
            # daemon is the host's, reached over a mounted socket. Without this
            # translation Docker silently creates an empty directory at a path
            # that only exists inside our own container, and the Lab opens on an
            # empty workspace instead of the project's files.
            "volumes": {
                to_host_path(str(host_path)): {"bind": container_path, "mode": "rw"}
                for host_path, container_path in (self.config.mounts or {}).items()
            },
        }
        if self.config.entrypoint:
            kwargs["entrypoint"] = self.config.entrypoint
        if not self.config.network:
            kwargs["network_mode"] = "none"

        gpus = self._requested_gpus(client)
        if gpus:
            kwargs["device_requests"] = [self._device_request(gpus)]
            # /dev/shm is 64MB by default, far too small for PyTorch DataLoader
            # workers — they communicate through it and die with a bare "Bus
            # error" that reads like a hardware fault.
            kwargs["shm_size"] = "16g"

        self._container = client.containers.run(**kwargs)
        self._host_port = self._resolve_port()
        logger.info(f"| 🔬 Science container {self._container.short_id} on 127.0.0.1:{self._host_port}"
                    + (f" with GPUs ({gpus})" if gpus else " (no GPUs)"))

    async def destroy(self) -> None:
        container, self._container, self._host_port = self._container, None, None
        if container is None:
            return

        def _remove() -> None:
            try:
                container.remove(force=True)
            except Exception as exc:  # noqa: BLE001 — auto_remove may have won the race
                logger.warning(f"| ⚠️ Science container removal: {exc}")

        await asyncio.to_thread(_remove)
        logger.info(f"| ⚫ Science container {container.short_id} removed")

    async def is_alive(self) -> bool:
        if self._container is None:
            return False

        def _check() -> bool:
            try:
                self._container.reload()
                return self._container.status == "running"
            except Exception:  # noqa: BLE001 — gone counts as not alive
                return False

        return await asyncio.to_thread(_check)

    # -------------------------------------------------------------------- urls
    async def lab_url(self) -> str:
        """Base URL of JupyterLab on the host, e.g. ``http://127.0.0.1:41293``."""
        if self._host_port is None:
            raise RuntimeError("The science sandbox is not running")
        return f"http://127.0.0.1:{self._host_port}"

    async def expose_port(self, port: int) -> str:
        """Only 8888 is published.

        Anything else a notebook starts is reached through jupyter-server-proxy
        on that same port, which is why the container publishes exactly one.
        """
        if port != LAB_PORT:
            raise ValueError(f"The science container publishes only {LAB_PORT}; "
                             f"reach {port} through jupyter-server-proxy")
        return await self.lab_url()

    # ------------------------------------------------------------------ exec
    async def run_command(self, command: str, *, timeout: Optional[int] = None,
                          cwd: Optional[str] = None, **_: Any) -> ExecResult:
        """Run a shell command inside the workstation."""
        if self._container is None:
            return ExecResult(success=False, error="The science sandbox is not running")

        def _exec() -> ExecResult:
            # `bash -c`, not `-lc`: a LOGIN shell re-reads /etc/profile, which
            # resets PATH to the system default and throws away the image's
            # /opt/conda/bin — so `python` was not found in the very container
            # built around that Python.
            result = self._container.exec_run(
                ["bash", "-c", command], workdir=cwd, demux=True)
            stdout, stderr = result.output if isinstance(result.output, tuple) else (result.output, None)
            return ExecResult(
                success=result.exit_code == 0,
                stdout=(stdout or b"").decode(errors="replace"),
                stderr=(stderr or b"").decode(errors="replace"),
                exit_code=result.exit_code)

        try:
            return await asyncio.wait_for(asyncio.to_thread(_exec), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(success=False, error=f"Command exceeded {timeout}s")
        except Exception as exc:  # noqa: BLE001 — a dead container is a failed result
            return ExecResult(success=False, error=str(exc))

    async def gpu_status(self) -> List[Dict[str, Any]]:
        """What nvidia-smi sees from inside — empty if the container has no GPUs.

        Read from inside rather than from the host on purpose: the point is what
        this container can actually use, which is what the device request
        granted it.
        """
        result = await self.run_command(
            "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu "
            "--format=csv,noheader,nounits", timeout=20)
        if not result.success:
            return []
        gpus: List[Dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            fields = [part.strip() for part in line.split(",")]
            if len(fields) != 5 or not fields[0].isdigit():
                continue
            gpus.append({
                "index": int(fields[0]), "name": fields[1],
                "memory_used_mb": int(fields[2]), "memory_total_mb": int(fields[3]),
                "utilization_percent": int(fields[4]),
            })
        return gpus

    # ------------------------------------------------------------- internals
    def _requested_gpus(self, client: Any = None) -> Optional[str]:
        """What to ask for, or None when the daemon cannot provide GPUs.

        Asking a daemon without the nvidia runtime makes the container fail to
        start, and a workstation with no GPUs beats no workstation at all. The
        daemon is what is checked — not ``nvidia-smi`` on this side, which the
        framework's own container does not ship.
        """
        requested = self.config.gpus or os.environ.get("AGENTEVOLVER_SCIENCE_GPUS") or "all"
        if requested in ("", "none", "0"):
            return None
        try:
            runtimes = (client or self._client()).info().get("Runtimes") or {}
        except Exception as exc:  # noqa: BLE001 — no daemon is answered elsewhere
            logger.warning(f"| ⚠️ Could not ask Docker about GPU runtimes: {exc}")
            return None
        if "nvidia" not in runtimes:
            logger.info("| ℹ️ Docker has no nvidia runtime; the science container starts without GPUs")
            return None
        return requested

    @staticmethod
    def _device_request(gpus: str) -> Any:
        """Turn a ``--gpus``-style string into the SDK's DeviceRequest.

        ``all`` is count=-1; ``device=0,1`` names them. This is the SDK's
        equivalent of the CLI flag, and the reason this sandbox exists —
        opensandbox has nowhere to put it.
        """
        from docker.types import DeviceRequest  # noqa: PLC0415

        if gpus == "all":
            return DeviceRequest(count=-1, capabilities=[["gpu"]])
        if gpus.startswith("device="):
            return DeviceRequest(device_ids=gpus.removeprefix("device=").split(","),
                                 capabilities=[["gpu"]])
        if gpus.isdigit():
            return DeviceRequest(count=int(gpus), capabilities=[["gpu"]])
        return DeviceRequest(count=-1, capabilities=[["gpu"]])

    def _resolve_port(self) -> int:
        """The ephemeral host port Docker published 8888 on."""
        self._container.reload()
        bindings = (self._container.attrs.get("NetworkSettings", {})
                    .get("Ports", {}).get(f"{LAB_PORT}/tcp") or [])
        for binding in bindings:
            port = binding.get("HostPort")
            if port and str(port).isdigit():
                return int(port)
        raise RuntimeError(f"Docker published no host port for {LAB_PORT}")

    def _ensure_image(self, client: Any, image: str) -> None:
        """Build docker/science/ on first use, like the vscode sandbox does."""
        if image != _IMAGE:
            return  # a custom image is the caller's to provide
        try:
            client.images.get(image)
            return
        except Exception:  # noqa: BLE001 — not built yet
            pass
        if not (_REPO_ROOT / _DOCKERFILE).exists():
            logger.warning(f"| ⚠️ science: Dockerfile not found at {_REPO_ROOT / _DOCKERFILE}")
            return
        logger.info(f"| 🐳 Building {image} (first use; this takes a while)…")
        try:
            # The context is read on THIS side and uploaded, so it is our own
            # path — unlike a bind mount, it needs no host translation. The repo
            # root, because the image is built FROM agentevolver/base.
            client.images.build(path=str(_REPO_ROOT), dockerfile=_DOCKERFILE, tag=image, rm=True)
        except Exception as exc:  # noqa: BLE001 — a failed build is reported, not raised
            logger.warning(f"| ⚠️ science: docker build failed: {exc}")


__all__ = ["ScienceSandbox", "LAB_PORT"]
