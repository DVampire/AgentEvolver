"""OpenSandbox server lifecycle manager.

A single instance of this class manages the opensandbox-server process for the
entire application. Multiple sandbox-based environments share one server.
"""

import asyncio
import os
import shutil
import subprocess
import sys
from typing import Optional

import httpx

from src.logger import logger


class SandboxServerManager:
    """Manages the lifecycle of a local opensandbox-server process.

    Usage::

        manager = SandboxServerManager()
        await manager.ensure_running()   # idempotent — starts server if needed
        ...
        await manager.shutdown()         # called once on global cleanup
    """

    def __init__(
        self,
        domain: str = "localhost:8080",
        server_bin: str = "opensandbox-server",
        startup_timeout: float = 30.0,
        poll_interval: float = 0.5,
    ):
        self.domain = domain
        self.server_bin = server_bin
        self.startup_timeout = startup_timeout
        self.poll_interval = poll_interval

        self._process: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------ public

    async def ensure_running(self) -> None:
        """Start the server if it is not already reachable.

        Idempotent — safe to call from multiple environments.
        """
        if await self.is_healthy():
            logger.info(f"| 📦 opensandbox-server already running at {self.domain}")
            return

        await self._start()
        await self._wait_until_ready()

    async def shutdown(self) -> None:
        """Terminate the server process if we started it."""
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=10)
            logger.info("| 🛑 opensandbox-server stopped")
        except Exception as e:
            logger.warning(f"| ⚠️ Error stopping opensandbox-server: {e}")
            self._process.kill()
        finally:
            self._process = None

    async def is_healthy(self) -> bool:
        """Return True if the server responds on its health endpoint."""
        url = f"http://{self.domain}/healthz"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                return resp.status_code < 500
        except Exception:
            return False

    # ------------------------------------------------------------------ private

    def _write_config(self) -> None:
        """Write ~/.sandbox.toml before starting the server.

        Drops security options (drop_capabilities, no_new_privileges, pids_limit)
        that are blocked by restrictive Docker authz plugins on some hosts.
        Existing config is overwritten only if it exists; otherwise created fresh.
        """
        config_path = os.path.expanduser("~/.sandbox.toml")

        # Read existing content if present so we preserve user customisations
        try:
            with open(config_path, "r") as f:
                content = f.read()
        except FileNotFoundError:
            # Run init-config to create a baseline config, then patch it
            bin_path = shutil.which(self.server_bin) or os.path.join(
                os.path.dirname(sys.executable), self.server_bin
            )
            subprocess.run(
                [bin_path, "init-config", config_path, "--example", "docker"],
                check=True, capture_output=True,
            )
            with open(config_path, "r") as f:
                content = f.read()

        # Patch the [docker] section — replace the three problematic keys
        import re

        def _replace_or_append(text: str, key: str, new_value: str) -> str:
            pattern = rf"^({re.escape(key)}\s*=\s*).*$"
            replacement = f"{key} = {new_value}"
            new_text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
            if count == 0:
                # Key not present — append under [docker] section
                new_text = re.sub(
                    r"(\[docker\])", rf"\1\n{replacement}", new_text, count=1
                )
            return new_text

        content = _replace_or_append(content, "drop_capabilities", "[]")
        content = _replace_or_append(content, "no_new_privileges", "false")
        # Remove pids_limit line entirely so it falls back to the default (4096)
        content = re.sub(r"^pids_limit\s*=.*\n?", "", content, flags=re.MULTILINE)

        with open(config_path, "w") as f:
            f.write(content)

        logger.info(f"| 🔧 opensandbox-server config written to {config_path}")

    async def _start(self) -> None:
        """Write config, then locate and launch the opensandbox-server binary."""
        bin_path = shutil.which(self.server_bin)
        if bin_path is None:
            # Also search alongside the current Python interpreter (conda/venv envs)
            python_bin_dir = os.path.dirname(sys.executable)
            candidate = os.path.join(python_bin_dir, self.server_bin)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                bin_path = candidate
        if bin_path is None:
            raise RuntimeError(
                f"opensandbox-server binary not found (looked for '{self.server_bin}'). "
                "Install it with: pip install opensandbox-server"
            )

        self._write_config()

        env = os.environ.copy()
        env.setdefault("OPENSANDBOX_INSECURE_SERVER", "YES")

        logger.info(f"| 🚀 Starting opensandbox-server ({bin_path})")
        self._process = subprocess.Popen(
            [bin_path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    async def _wait_until_ready(self) -> None:
        """Poll the health endpoint until the server is ready or timeout."""
        elapsed = 0.0
        while elapsed < self.startup_timeout:
            if await self.is_healthy():
                logger.info(
                    f"| ✅ opensandbox-server ready at {self.domain} "
                    f"(took {elapsed:.1f}s)"
                )
                return
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval

        raise TimeoutError(
            f"opensandbox-server did not become ready within {self.startup_timeout}s"
        )
