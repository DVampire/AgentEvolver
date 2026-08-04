"""Desktop ("computer") sandbox — a full Linux desktop the agent drives with
mouse and keyboard, watched live over noVNC.

Unlike the browser sandbox (which drives Chrome over CDP), this one is controlled
through the sandbox's ``run_command`` channel: ``xdotool`` injects input and
``scrot`` captures the screen. The desktop stack (Xvfb + XFCE + x11vnc +
websockify) is started on demand by ``start-desktop`` after the container is up,
so the OpenSandbox agent's entrypoint stays intact and run_command keeps working.

Provider abstraction (OSWorld-style): the environment on top never changes; only
the backend does. ``docker-linux`` (this file) is the default — a container that
fits opensandbox, spawns in seconds, and pools. ``vm-windows`` / ``vm-macos`` are
future heavyweight VM providers (for OS-specific apps) that would implement the
same ``start_desktop`` / ``vnc_ws_url`` / ``run`` surface against a VM instead.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Optional

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.default.base import OpenSandbox
from agentevolver.sandbox.types import SandboxConfig

DISPLAY = ":99"
VNC_PORT = 5900
NOVNC_PORT = 6080
_IMAGE = "agentevolver/computer:latest"
# repo root: .../agentevolver/sandbox/default/computer.py -> parents[3]
_DOCKERFILE_DIR = Path(__file__).resolve().parents[3] / "docker" / "computer"


@SANDBOX.register_module(name="computer", force=True)
class DesktopSandbox(OpenSandbox):
    """A Linux desktop container: run_command-driven input, noVNC live view."""

    name: str = "computer"
    description: str = "A full Linux desktop driven with mouse/keyboard, watchable over noVNC."
    default_image: str = _IMAGE

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        if config is None:
            config = SandboxConfig(**kwargs)
        if not config.timeout_minutes or config.timeout_minutes == 10:
            config.timeout_minutes = 60  # desktop sessions are long-lived
        super().__init__(config)
        self._desktop_started = False

    async def start(self) -> None:
        await self._ensure_image()
        await super().start()

    async def start_desktop(self, *, width: int = 1280, height: int = 800) -> None:
        """Bring up Xvfb + XFCE + x11vnc + websockify inside the container. Idempotent."""
        if self._desktop_started:
            return
        geometry = f"{width}x{height}x24"
        result = await self.run_command(
            "/usr/local/bin/start-desktop",
            env={"DISPLAY_NUM": DISPLAY, "SCREEN_GEOMETRY": geometry,
                 "VNC_PORT": str(VNC_PORT), "NOVNC_PORT": str(NOVNC_PORT)},
            timeout=120,
        )
        if not result.success:
            raise RuntimeError(f"Failed to start desktop: {result.error or result}")
        self._desktop_started = True
        logger.info(f"| 🖥️  Desktop started ({geometry})")

    async def run(self, command: str, *, timeout: int = 60):
        """Run a command against the desktop display (DISPLAY already exported)."""
        return await self.run_command(command, env={"DISPLAY": DISPLAY}, timeout=timeout)

    async def vnc_ws_url(self) -> str:
        """The websockify WebSocket URL the frontend's noVNC client connects to."""
        sb = self._require()
        endpoint = await sb.get_endpoint(NOVNC_PORT)
        host = getattr(endpoint, "endpoint", str(endpoint))  # 127.0.0.1:PORT/proxy/6080
        proxy_host = host.split("/proxy/")[0]
        from agentevolver.port import port_manager
        try:
            port_manager.register(f"{self.name}:novnc", int(proxy_host.rsplit(":", 1)[-1]), kind="env")
        except (ValueError, IndexError):
            pass
        return f"ws://{proxy_host}/proxy/{NOVNC_PORT}/websockify"

    # ------------------------------------------------------------- image build
    async def _ensure_image(self) -> None:
        image = self.config.image or self.default_image
        if image != _IMAGE or not shutil.which("docker"):
            return
        if await self._docker(["image", "inspect", image], quiet=True) == 0:
            return
        if not (_DOCKERFILE_DIR / "Dockerfile").exists():
            logger.warning(f"| ⚠️ computer: Dockerfile not found at {_DOCKERFILE_DIR}")
            return
        logger.info(f"| 🐳 Building {image} from {_DOCKERFILE_DIR} (first use; a few minutes)…")
        code = await self._docker(["build", "-t", image, str(_DOCKERFILE_DIR)])
        if code != 0:
            logger.warning(f"| ⚠️ computer: docker build failed (exit {code}); the sandbox may not start")

    @staticmethod
    async def _docker(args: list[str], *, quiet: bool = False) -> int:
        sink = asyncio.subprocess.DEVNULL if quiet else None
        proc = await asyncio.create_subprocess_exec("docker", *args, stdout=sink, stderr=sink)
        return await proc.wait()
