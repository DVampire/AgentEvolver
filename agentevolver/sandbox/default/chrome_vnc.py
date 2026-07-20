"""Headful Chrome + noVNC sandbox.

Behaves like :class:`PlaywrightSandbox` (drives Chrome over CDP), but runs the
browser HEADFUL on a virtual display with a VNC → websockify bridge, so the live
view can be watched over noVNC.  The image is built from ``docker/chrome-vnc/``
on first use (OpenSandbox runs over local Docker, so a locally-built tag works).

Exposes two proxied ports:
  9222  CDP  (inherited ``cdp_ws_url`` — Playwright connects here)
  6080  websockify (``vnc_ws_url`` — the frontend's noVNC client connects here)
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Optional

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.default.playwright import PlaywrightSandbox
from agentevolver.sandbox.types import SandboxConfig
from agentevolver.port import NOVNC as NOVNC_PORT

_IMAGE = "agentevolver/chrome-vnc:latest"
# repo root: .../agentevolver/sandbox/default/chrome_vnc.py -> parents[3]
_DOCKERFILE_DIR = Path(__file__).resolve().parents[3] / "docker" / "chrome-vnc"


@SANDBOX.register_module(name="chrome-vnc", force=True)
class ChromeVncSandbox(PlaywrightSandbox):
    """Headful Chrome on a virtual display with a noVNC live view, reachable over CDP."""

    name: str = "chrome-vnc"
    description: str = "Headful Chrome with a noVNC live view, reachable over the DevTools protocol."
    default_image: str = _IMAGE
    # Our image sets its own ENTRYPOINT (the VNC launcher); do not override it.
    default_entrypoint = None

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        super().__init__(config=config, **kwargs)

    async def start(self) -> None:
        await self._ensure_image()
        await super().start()

    async def _ensure_image(self) -> None:
        """Build the chrome-vnc image from docker/chrome-vnc/ if it isn't present."""
        image = self.config.image or self.default_image
        if image != _IMAGE or not shutil.which("docker"):
            return  # custom image, or no Docker to build with — leave it to the runtime
        if await self._docker(["image", "inspect", image], quiet=True) == 0:
            return  # already built
        if not (_DOCKERFILE_DIR / "Dockerfile").exists():
            logger.warning(f"| ⚠️ chrome-vnc: Dockerfile not found at {_DOCKERFILE_DIR}")
            return
        logger.info(f"| 🐳 Building {image} from {_DOCKERFILE_DIR} (first use; this can take a few minutes)…")
        code = await self._docker(["build", "-t", image, str(_DOCKERFILE_DIR)])
        if code != 0:
            logger.warning(f"| ⚠️ chrome-vnc: docker build failed (exit {code}); the sandbox may not start")

    @staticmethod
    async def _docker(args: list[str], *, quiet: bool = False) -> int:
        sink = asyncio.subprocess.DEVNULL if quiet else None
        proc = await asyncio.create_subprocess_exec("docker", *args, stdout=sink, stderr=sink)
        return await proc.wait()

    async def vnc_ws_url(self) -> str:
        """Return the websockify WebSocket URL the frontend's noVNC client connects to."""
        sb = self._require()
        endpoint = await sb.get_endpoint(NOVNC_PORT)
        host = getattr(endpoint, "endpoint", str(endpoint))  # e.g. 127.0.0.1:PORT/proxy/6080
        proxy_host = host.split("/proxy/")[0]
        return f"ws://{proxy_host}/proxy/{NOVNC_PORT}/websockify"
