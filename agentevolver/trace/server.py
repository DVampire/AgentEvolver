"""TraceManager — singleton facade for the whole trace subsystem.

Lifecycle::

    await trace_manager.initialize(log_root="output/example/log/trace")
    await trace_manager.start()          # starts writer + FastAPI server
    ...
    await trace_manager.emit(event)      # non-blocking async emit
    ...
    await trace_manager.stop()

The FastAPI server runs in a background asyncio task on port 8765.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Optional

from agentevolver.logger import logger
from agentevolver.queue import AsyncQueue
from agentevolver.trace.types import TraceEvent
from agentevolver.trace.writer import TraceWriter
from agentevolver.utils import Singleton

WEB_PORT = 8765


class TraceManager(metaclass=Singleton):
    """Singleton that owns the event queue, writer, and web server."""

    def __init__(self) -> None:
        self._log_root: Optional[str] = None
        self._queue: Optional[AsyncQueue[TraceEvent]] = None
        self._writer: Optional[TraceWriter] = None
        self._server_task: Optional[asyncio.Task] = None
        self._ws_manager = None   # set after build_app
        self._initialized: bool = False
        self._running: bool = False
        self._subscribers = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, log_root: Optional[str] = None) -> None:
        """Set log_root and create queue / writer.  Idempotent.

        If log_root is omitted, defaults to ``{config.log_root}/trace``.
        """
        if self._initialized:
            return
        if log_root is None:
            from agentevolver.config import config
            log_root = os.path.join(config.log_root, "trace")
        self._log_root = log_root
        os.makedirs(log_root, exist_ok=True)

        self._queue = AsyncQueue[TraceEvent](maxsize=20_000)
        self._writer = TraceWriter(log_root=log_root, queue=self._queue)
        self._initialized = True
        logger.info(f"| 🔍 TraceManager initialised (log_root={log_root})")

    async def start(self, *, start_server: bool = True) -> None:
        """Start the writer consumer loop and, optionally, the Trace web server."""
        if not self._initialized:
            raise RuntimeError("TraceManager.initialize() must be called first")
        if self._running:
            return

        self._writer.start()
        self._uvicorn_server = None
        if start_server:
            await self._ensure_ui_built()
            self._server_task = asyncio.create_task(
                self._run_web_server(), name="trace-web-server"
            )
        self._running = True
        if start_server:
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

    async def emit(self, event: TraceEvent) -> None:
        """Emit a trace event.  Never blocks on the caller, never raises."""
        if not self._running or self._queue is None:
            return
        self._queue.emit(event)
        # Push to WebSocket clients if the server is up
        if self._ws_manager is not None:
            asyncio.ensure_future(self._ws_manager.broadcast(event))
        for subscriber in tuple(self._subscribers):
            try:
                result = subscriber(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"| ⚠️  Trace subscriber failed: {exc}")

    def subscribe(self, callback) -> None:
        """Receive every emitted event without coupling callers to a transport."""
        self._subscribers.add(callback)

    def unsubscribe(self, callback) -> None:
        self._subscribers.discard(callback)

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
            logger.warning(f"| ⚠️  npm install failed: {stderr.decode()}")
            return

        build = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=ui_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await build.communicate()
        if build.returncode != 0:
            logger.warning(f"| ⚠️  npm run build failed: {stderr.decode()}")
            return

        logger.info("| ✅ Trace UI built successfully")

    # ------------------------------------------------------------------
    # Web server
    # ------------------------------------------------------------------

    async def _run_web_server(self) -> None:
        """Launch uvicorn in the current event loop (background task)."""
        try:
            import uvicorn
            from agentevolver.trace.app import build_app

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
