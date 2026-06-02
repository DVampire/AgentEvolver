"""Playwright-based browser service.

Supports two launch modes:
  - Local (default): launches a Chromium process via Playwright directly.
  - OpenSandbox: spins up an opensandbox/chrome container and connects via CDP.
"""

import asyncio
import base64
from datetime import timedelta
from typing import Dict, Any, List, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from src.logger import logger
from src.environment.types import ActionResult


class BrowserService:
    """Browser service backed directly by Playwright."""

    def __init__(
        self,
        headless: bool = True,
        viewport: Dict[str, int] = None,
        use_sandbox: bool = False,
        sandbox_domain: str = "localhost:8080",
        sandbox_api_key: Optional[str] = None,
        sandbox_image: str = "opensandbox/chrome:latest",
        sandbox_timeout_minutes: int = 30,
    ):
        self.headless = headless
        self.viewport = viewport or {"width": 1024, "height": 768}
        self.use_sandbox = use_sandbox
        self.sandbox_domain = sandbox_domain
        self.sandbox_api_key = sandbox_api_key
        self.sandbox_image = sandbox_image
        self.sandbox_timeout_minutes = sandbox_timeout_minutes
        self.sandbox_server_bin = "opensandbox-server"

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._sandbox = None  # opensandbox Sandbox instance

    async def start(self):
        """Launch the browser and open a default page."""
        try:
            self._playwright = await async_playwright().start()

            if self.use_sandbox:
                await self._start_sandbox()
            else:
                await self._start_local()

            self._page = await self._context.new_page()
            await self._page.goto("https://www.google.com")
            await asyncio.sleep(1)
            logger.info("| 🌐 BrowserService started")
        except Exception as e:
            logger.error(f"| ❌ Failed to start browser: {e}")
            raise

    async def _start_local(self):
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(viewport=self.viewport)
        logger.info("| 🖥️  Local Chromium launched")

    async def _start_sandbox(self):
        """Connect to a Chrome container managed by opensandbox-server.

        EnvironmentContextManager starts the server before environments are built.
        When BrowserService is used standalone (e.g. in tests), we ensure the
        server is running here as a fallback.
        """
        from src.environment.sandbox import SandboxServerManager
        from opensandbox.sandbox import Sandbox
        from opensandbox.config import ConnectionConfig

        await SandboxServerManager(domain=self.sandbox_domain, server_bin=self.sandbox_server_bin).ensure_running()

        connection_config = ConnectionConfig(
            domain=self.sandbox_domain,
            api_key=self.sandbox_api_key,
            request_timeout=timedelta(seconds=60),
        )
        self._sandbox = await Sandbox.create(
            self.sandbox_image,
            timeout=timedelta(minutes=self.sandbox_timeout_minutes),
            entrypoint=["/entrypoint"],
            connection_config=connection_config,
        )
        devtools = await self._sandbox.get_endpoint(9222)
        proxy_base = f"http://{devtools.endpoint}"  # e.g. http://127.0.0.1:40697/proxy/9222
        logger.info(f"| 📦 OpenSandbox Chrome DevTools proxy: {proxy_base}")

        # Fetch /json/version through the proxy and rewrite the ws URL so it also
        # goes through the proxy (the raw ws URL points at the container-internal port)
        import httpx as _httpx

        ws_url = None
        for attempt in range(15):
            try:
                async with _httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{proxy_base}/json/version")
                    data = resp.json()
                    raw_ws = data.get("webSocketDebuggerUrl", "")
                    # raw_ws looks like ws://127.0.0.1:40697/devtools/browser/<id>
                    # We need ws://127.0.0.1:40697/proxy/9222/devtools/browser/<id>
                    proxy_host = devtools.endpoint.split("/proxy/")[0]  # 127.0.0.1:40697
                    devtools_path = raw_ws.split(proxy_host, 1)[-1]     # /devtools/browser/<id>
                    ws_url = f"ws://{proxy_host}/proxy/9222{devtools_path}"
                    logger.info(f"| 📦 CDP WebSocket URL: {ws_url}")
                    break
            except Exception:
                if attempt == 14:
                    raise
                await asyncio.sleep(2)
                logger.info(f"| ⏳ Waiting for Chrome DevTools to be ready (attempt {attempt + 1}/15)")

        self._browser = await self._playwright.chromium.connect_over_cdp(ws_url)
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context(viewport=self.viewport)

    async def stop(self):
        """Close the browser."""
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            if self._sandbox:
                await self._sandbox.kill()
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._sandbox = None
            logger.info("| 🛑 BrowserService stopped")
        except Exception as e:
            logger.error(f"| ❌ Error stopping browser: {e}")

    # ------------------------------------------------------------------ helpers

    async def _screenshot_b64(self) -> str:
        """Return a base64-encoded PNG screenshot of the current page."""
        data = await self._page.screenshot(type="png")
        return base64.b64encode(data).decode("utf-8")

    async def _tabs(self) -> List[str]:
        """Return URLs of all open pages in the context."""
        return [p.url for p in self._context.pages]

    def _unavailable(self, action: str) -> ActionResult:
        return ActionResult(
            success=False,
            message="Browser not available",
            extra={"error": "Browser not available", "action": action},
        )

    # ------------------------------------------------------------------ actions

    async def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        if not self._page:
            return self._unavailable("click")
        try:
            await self._page.mouse.click(x, y, button=button)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Clicked at ({x}, {y}) with {button} button",
                extra={"screenshot": screenshot, "x": x, "y": y, "button": button},
            )
        except Exception as e:
            logger.error(f"| ❌ click failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def double_click(self, x: int, y: int) -> ActionResult:
        if not self._page:
            return self._unavailable("double_click")
        try:
            await self._page.mouse.dblclick(x, y)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Double-clicked at ({x}, {y})",
                extra={"screenshot": screenshot, "x": x, "y": y},
            )
        except Exception as e:
            logger.error(f"| ❌ double_click failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> ActionResult:
        if not self._page:
            return self._unavailable("scroll")
        try:
            await self._page.mouse.move(x, y)
            await self._page.mouse.wheel(scroll_x, scroll_y)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Scrolled at ({x}, {y}) by ({scroll_x}, {scroll_y})",
                extra={"screenshot": screenshot, "x": x, "y": y, "scroll_x": scroll_x, "scroll_y": scroll_y},
            )
        except Exception as e:
            logger.error(f"| ❌ scroll failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def type(self, text: str) -> ActionResult:
        if not self._page:
            return self._unavailable("type")
        try:
            await self._page.keyboard.type(text)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Typed: {text}",
                extra={"screenshot": screenshot, "text": text},
            )
        except Exception as e:
            logger.error(f"| ❌ type failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def wait(self, ms: int) -> ActionResult:
        if not self._page:
            return self._unavailable("wait")
        try:
            await asyncio.sleep(ms / 1000.0)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Waited {ms}ms",
                extra={"screenshot": screenshot, "ms": ms},
            )
        except Exception as e:
            logger.error(f"| ❌ wait failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def move(self, x: int, y: int) -> ActionResult:
        if not self._page:
            return self._unavailable("move")
        try:
            await self._page.mouse.move(x, y)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Moved to ({x}, {y})",
                extra={"screenshot": screenshot, "x": x, "y": y},
            )
        except Exception as e:
            logger.error(f"| ❌ move failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def keypress(self, keys: List[str]) -> ActionResult:
        if not self._page:
            return self._unavailable("keypress")
        try:
            combo = "+".join(keys)
            await self._page.keyboard.press(combo)
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Pressed {keys}",
                extra={"screenshot": screenshot, "keys": keys},
            )
        except Exception as e:
            logger.error(f"| ❌ keypress failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def drag(self, path: List[List[int]]) -> ActionResult:
        if not self._page:
            return self._unavailable("drag")
        try:
            if len(path) < 2:
                raise ValueError("Drag path must have at least 2 points")
            start = path[0]
            await self._page.mouse.move(start[0], start[1])
            await self._page.mouse.down()
            for point in path[1:]:
                await self._page.mouse.move(point[0], point[1])
            await self._page.mouse.up()
            screenshot = await self._screenshot_b64()
            return ActionResult(
                success=True,
                message=f"Dragged along {len(path)} points",
                extra={"screenshot": screenshot, "path": path},
            )
        except Exception as e:
            logger.error(f"| ❌ drag failed: {e}")
            return ActionResult(success=False, message=str(e), extra={"error": str(e)})

    async def get_state(self) -> Dict[str, Any]:
        """Return current page state including a base64 screenshot."""
        if not self._page:
            return {"url": None, "title": None, "tabs": [], "screenshot": None}
        try:
            url = self._page.url
            title = await self._page.title()
            tabs = await self._tabs()
            screenshot = await self._screenshot_b64()
            return {"url": url, "title": title, "tabs": tabs, "screenshot": screenshot}
        except Exception as e:
            logger.error(f"| ❌ get_state failed: {e}")
            return {"url": None, "title": None, "tabs": [], "screenshot": None}
