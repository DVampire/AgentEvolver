"""TraceManager — singleton facade for the whole trace subsystem.

Lifecycle::

    await trace_manager.initialize(workdir="workdir/trace")
    await trace_manager.start()          # starts writer + FastAPI server
    ...
    trace_manager.emit(event)            # fire-and-forget from anywhere
    ...
    await trace_manager.stop()

The FastAPI server runs in a background asyncio task on port 8765.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from src.logger import logger
from src.queue import AsyncQueue
from src.trace.types import TraceEvent
from src.trace.writer import TraceWriter
from src.utils import Singleton

WEB_PORT = 8765


class TraceManager(metaclass=Singleton):
    """Singleton that owns the event queue, writer, and web server."""

    def __init__(self) -> None:
        self._workdir: Optional[str] = None
        self._queue: Optional[AsyncQueue[TraceEvent]] = None
        self._writer: Optional[TraceWriter] = None
        self._server_task: Optional[asyncio.Task] = None
        self._ws_manager = None   # set after build_app
        self._initialized: bool = False
        self._running: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, workdir: Optional[str] = None) -> None:
        """Set workdir and create queue / writer.  Idempotent.

        If workdir is omitted, defaults to ``{config.workdir}/trace``.
        """
        if self._initialized:
            return
        if workdir is None:
            from src.config import config
            workdir = os.path.join(config.workdir, "trace")
        self._workdir = workdir
        os.makedirs(workdir, exist_ok=True)

        self._queue = AsyncQueue[TraceEvent](maxsize=20_000)
        self._writer = TraceWriter(workdir=workdir, queue=self._queue)
        self._initialized = True
        logger.info(f"| 🔍 TraceManager initialised (workdir={workdir})")

    async def start(self) -> None:
        """Start the writer consumer loop and the FastAPI web server."""
        if not self._initialized:
            raise RuntimeError("TraceManager.initialize() must be called first")
        if self._running:
            return

        await self._ensure_ui_built()

        self._writer.start()
        self._uvicorn_server = None

        self._server_task = asyncio.create_task(
            self._run_web_server(), name="trace-web-server"
        )
        self._running = True
        logger.info(f"| 🌐 Trace web UI: http://localhost:{WEB_PORT}")

    async def stop(self) -> None:
        """Drain queue, flush writer, stop web server gracefully."""
        if not self._running:
            return
        self._running = False

        if self._writer:
            await self._writer.stop()

        # Use uvicorn's built-in shutdown to avoid CancelledError noise
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

        if self._server_task:
            try:
                await asyncio.wait_for(self._server_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                self._server_task.cancel()
                try:
                    await self._server_task
                except (asyncio.CancelledError, Exception):
                    pass

        logger.info("| ⏹️  TraceManager stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event: TraceEvent) -> None:
        """Fire-and-forget event emission.  Never blocks, never raises."""
        if not self._running or self._queue is None:
            return
        self._queue.emit(event)
        # Push to WebSocket clients if the server is up
        if self._ws_manager is not None:
            asyncio.ensure_future(self._ws_manager.broadcast(event))

    @property
    def writer(self) -> Optional[TraceWriter]:
        return self._writer

    @property
    def port(self) -> int:
        return WEB_PORT

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    async def _ensure_ui_built(self) -> None:
        """Auto-install and build the React UI if dist/ is missing."""
        ui_dir = os.path.join(os.path.dirname(__file__), "ui")
        dist_dir = os.path.join(ui_dir, "dist")

        if os.path.isdir(dist_dir):
            return

        pkg_json = os.path.join(ui_dir, "package.json")
        if not os.path.isfile(pkg_json):
            logger.warning("| ⚠️  Trace UI source not found — web UI disabled")
            return

        # Check npm is available
        npm_check = await asyncio.create_subprocess_exec(
            "npm", "--version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await npm_check.wait()
        if npm_check.returncode != 0:
            logger.warning("| ⚠️  npm not found — trace web UI will not be built")
            return

        logger.info("| 📦 Building trace UI (first run)...")

        install = await asyncio.create_subprocess_exec(
            "npm", "install",
            cwd=ui_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await install.communicate()
        if install.returncode != 0:
            logger.warning(f"| ⚠️  npm install failed: {stderr.decode()[:200]}")
            return

        build = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=ui_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await build.communicate()
        if build.returncode != 0:
            logger.warning(f"| ⚠️  npm run build failed: {stderr.decode()[:200]}")
            return

        logger.info("| ✅ Trace UI built successfully")

    # ------------------------------------------------------------------
    # Web server
    # ------------------------------------------------------------------

    async def _run_web_server(self) -> None:
        """Launch uvicorn in the current event loop (background task)."""
        try:
            import uvicorn
            from src.trace.app import build_app

            app, self._ws_manager = build_app(writer=self._writer)
            uvicorn_config = uvicorn.Config(
                app=app,
                host="0.0.0.0",
                port=WEB_PORT,
                log_level="error",
                loop="none",
            )
            self._uvicorn_server = uvicorn.Server(uvicorn_config)
            await self._uvicorn_server.serve()
        except asyncio.CancelledError:
            pass
        except ImportError:
            logger.warning(
                "| ⚠️  uvicorn or fastapi not installed — trace web UI disabled. "
                "Run: pip install fastapi uvicorn[standard]"
            )
        except Exception as e:
            logger.error(f"| ❌ Trace web server error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

trace_manager = TraceManager()
