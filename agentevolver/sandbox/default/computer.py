"""Desktop ("computer") sandbox — a full Linux desktop the agent drives with
mouse and keyboard, watched live over noVNC.

Unlike the browser sandbox (which drives Chrome over CDP), this one is controlled
through the sandbox's ``run_command`` channel: ``xdotool`` injects input and
``scrot`` captures the screen. The desktop stack (Xvfb + GNOME + x11vnc +
websockify) is started on demand by ``start-desktop`` after the container is up.

It runs on ``DockerSandbox`` — no systemd, no privileges. That is a consequence of
the desktop's shape: mutter and tint2 rather than a GNOME *session*, which is the
only part that would have needed logind and therefore systemd as PID 1. The window
manager is still GNOME's, so the desktop looks like Ubuntu without the container
having to become a small virtual machine to host it.

Provider abstraction (OSWorld-style): the environment on top never changes; only
the backend does. ``docker-linux`` (this file) is the default. ``vm-windows`` /
``vm-macos`` are future heavyweight VM providers (for OS-specific apps) that would
implement the same ``start_desktop`` / ``vnc_ws_url`` / ``run`` surface.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path
from typing import Any, Optional

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.default.docker import DockerSandbox
from agentevolver.sandbox.types import SandboxConfig

DISPLAY = ":99"
VNC_PORT = 5900
NOVNC_PORT = 6080
_IMAGE = "agentevolver/computer:latest"
# repo root: .../agentevolver/sandbox/default/computer.py -> parents[3]
_DOCKERFILE_DIR = Path(__file__).resolve().parents[3] / "docker" / "computer"


@SANDBOX.register_module(name="computer", force=True)
class DesktopSandbox(DockerSandbox):
    """A Linux desktop container: run_command-driven input, noVNC live view."""

    name: str = "computer"
    description: str = "A full Linux desktop driven with mouse/keyboard, watchable over noVNC."
    default_image: str = _IMAGE

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        if config is None:
            config = SandboxConfig(**kwargs)
        if not config.image:
            config.image = _IMAGE
        if not config.timeout_minutes or config.timeout_minutes == 10:
            config.timeout_minutes = 60  # desktop sessions are long-lived
        # Published at creation because that is the only time a container port can be:
        # 0 asks Docker for a free host port, which `expose_port` reads back.
        config.publish_ports = dict(config.publish_ports or {}) or {NOVNC_PORT: 0, VNC_PORT: 0}
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
        await self._await_vnc_ready()
        self._desktop_started = True
        logger.info(f"| 🖥️  Desktop started ({geometry})")

    async def _await_vnc_ready(self, *, attempts: int = 40, delay: float = 0.5) -> None:
        """Block until websockify actually accepts connections.

        start-desktop backgrounds every service and returns immediately, so without
        this the desktop is declared up while its ports are still closed. Whoever
        connects first — the relay, right after `environment.open` — is refused, and
        the browser shows a dead canvas for a container that came up fine seconds
        later. Polled inside the container: the published host port exists from
        creation, so probing it from out here proves nothing about the listener.
        """
        probe = (
            f"for _ in $(seq 1 {attempts}); do "
            f"(exec 3<>/dev/tcp/127.0.0.1/{NOVNC_PORT}) 2>/dev/null && exit 0; "
            f"sleep {delay}; done; exit 1"
        )
        result = await self.run_command(
            f"bash -c {shlex.quote(probe)}", timeout=int(attempts * delay) + 30,
        )
        if not result.success:
            raise RuntimeError(
                f"desktop came up but noVNC never began listening on {NOVNC_PORT} "
                f"within {attempts * delay:.0f}s"
            )

    async def run(self, command: str, *, timeout: int = 60):
        """Run a command against the desktop display (DISPLAY already exported)."""
        return await self.run_command(command, env={"DISPLAY": DISPLAY}, timeout=timeout)

    async def vnc_ws_url(self) -> str:
        """The websockify WebSocket URL the frontend's noVNC client connects to.

        A published port, read back from Docker — there is no opensandbox proxy in front
        of this container, so the address is the mapping itself.
        """
        endpoint = await self.expose_port(NOVNC_PORT)   # http://127.0.0.1:<host port>
        host = endpoint.split("://", 1)[-1].rstrip("/")
        from agentevolver.port import port_manager
        try:
            port_manager.register(f"{self.name}:novnc", int(host.rsplit(":", 1)[-1]), type="env")
        except (ValueError, IndexError, TypeError):
            # Recording the port is bookkeeping; failing to is not a reason to withhold a
            # working desktop from the person waiting for it.
            pass
        return f"ws://{host}/websockify"

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
