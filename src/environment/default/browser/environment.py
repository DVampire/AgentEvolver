"""Browser environment backed by Playwright."""

import base64
import io
import os
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import ConfigDict, Field

from src.environment.default.browser.service import BrowserService
from src.environment.server import environment_manager
from src.environment.types import Environment, ScreenshotInfo
from src.logger import logger
from src.registry import ENVIRONMENT
from src.utils import ScreenshotService, assemble_project_path, dedent
from src.utils import encode_file_base64


def _b64_to_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))

@ENVIRONMENT.register_module(force=True)
class BrowserEnvironment(Environment):
    """Playwright-based browser environment."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="browser_environment")
    description: str = Field(default="Playwright browser environment for web automation")
    metadata: Dict[str, Any] = Field(default={
        "has_vision": True,
        "additional_rules": {
            "state": "The state of the browser environment including current URL, title, and open tabs.",
        },
    })
    require_grad: bool = Field(default=False)

    def __init__(
        self,
        base_dir: str = None,
        headless: bool = False,
        viewport: Optional[Dict[str, int]] = None,
        use_sandbox: bool = False,
        sandbox_domain: str = "localhost:8080",
        sandbox_api_key: Optional[str] = None,
        sandbox_image: str = "opensandbox/chrome:latest",
        sandbox_timeout_minutes: int = 30,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir) if base_dir else assemble_project_path("browser")
        self.headless = headless
        self.viewport = viewport or {"width": 1024, "height": 768}

        os.makedirs(self.base_dir, exist_ok=True)

        self._service = BrowserService(
            headless=headless,
            viewport=self.viewport,
            use_sandbox=use_sandbox,
            sandbox_domain=sandbox_domain,
            sandbox_api_key=sandbox_api_key,
            sandbox_image=sandbox_image,
            sandbox_timeout_minutes=sandbox_timeout_minutes,
        )
        self._step = 0
        self.screenshot: Optional[ScreenshotInfo] = None
        self.previous_screenshot: Optional[ScreenshotInfo] = None
        self._ss = ScreenshotService(base_dir=self.base_dir)

    # ------------------------------------------------------------------ lifecycle

    async def initialize(self) -> None:
        await self._service.start()
        logger.info(f"| 🌐 BrowserEnvironment ready at: {self.base_dir}")

    async def cleanup(self) -> None:
        await self._service.stop()
        logger.info("| 🧹 BrowserEnvironment cleaned up")

    # ------------------------------------------------------------------ helpers

    def _make_screenshot_info(self, b64: str, path: str, description: str) -> ScreenshotInfo:
        return ScreenshotInfo(
            transformed=False,
            screenshot=b64,
            screenshot_path=path,
            screenshot_description=description,
            transform_info=None,
        )

    async def _save_annotated(self, img: Image.Image, suffix: str) -> str:
        filename = f"step_{self._step:04d}_{suffix}.png"
        path = await self._ss.store_screenshot(img, self._step, filename)
        return path

    def _wrap(self, result, extra_override: Dict = None) -> Dict[str, Any]:
        extra = result.extra.copy() if result.extra else {}
        if extra_override:
            extra.update(extra_override)
        return {"success": result.success, "message": result.message, "extra": extra}

    # ------------------------------------------------------------------ actions

    @environment_manager.action(name="click", description="Click at specified coordinates on the page")
    async def click(self, x: int, y: int, button: str = "left", **kwargs) -> Dict[str, Any]:
        try:
            if self.screenshot:
                img = _b64_to_image(self.screenshot.screenshot)
                img = await self._ss.draw_cursor(img, x, y)
                path = await self._save_annotated(img, "click")
                self.previous_screenshot = self._make_screenshot_info(
                    encode_file_base64(file_path=path), path, f"Action: Click at ({x}, {y}) with {button} button"
                )
            self._step += 1
            result = await self._service.click(x, y, button)
            return self._wrap(result, {"x": x, "y": y, "button": button})
        except Exception as e:
            logger.error(f"| ❌ click failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="double_click", description="Double click at specified coordinates on the page")
    async def double_click(self, x: int, y: int, **kwargs) -> Dict[str, Any]:
        try:
            if self.screenshot:
                img = _b64_to_image(self.screenshot.screenshot)
                img = await self._ss.draw_cursor(img, x, y)
                path = await self._save_annotated(img, "double_click")
                self.previous_screenshot = self._make_screenshot_info(
                    encode_file_base64(file_path=path), path, f"Action: Double-click at ({x}, {y})"
                )
            self._step += 1
            result = await self._service.double_click(x, y)
            return self._wrap(result, {"x": x, "y": y})
        except Exception as e:
            logger.error(f"| ❌ double_click failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="scroll", description="Scroll at specified coordinates with given offsets")
    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int, **kwargs) -> Dict[str, Any]:
        try:
            if self.screenshot:
                img = _b64_to_image(self.screenshot.screenshot)
                img = await self._ss.draw_scroll(img, x, y, scroll_x, scroll_y)
                path = await self._save_annotated(img, "scroll")
                self.previous_screenshot = self._make_screenshot_info(
                    encode_file_base64(file_path=path), path,
                    f"Action: Scroll at ({x}, {y}) offset ({scroll_x}, {scroll_y})"
                )
            self._step += 1
            result = await self._service.scroll(x, y, scroll_x, scroll_y)
            return self._wrap(result, {"x": x, "y": y, "scroll_x": scroll_x, "scroll_y": scroll_y})
        except Exception as e:
            logger.error(f"| ❌ scroll failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="type", description="Type text at the current cursor position")
    async def type_text(self, text: str, **kwargs) -> Dict[str, Any]:
        try:
            self._step += 1
            result = await self._service.type(text)
            return self._wrap(result, {"text": text})
        except Exception as e:
            logger.error(f"| ❌ type failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="wait", description="Wait for specified milliseconds")
    async def wait(self, ms: int, **kwargs) -> Dict[str, Any]:
        try:
            self._step += 1
            result = await self._service.wait(ms)
            return self._wrap(result, {"ms": ms})
        except Exception as e:
            logger.error(f"| ❌ wait failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="move", description="Move mouse to specified coordinates")
    async def move(self, x: int, y: int, **kwargs) -> Dict[str, Any]:
        try:
            if self.screenshot:
                img = _b64_to_image(self.screenshot.screenshot)
                img = await self._ss.draw_cursor(img, x, y)
                path = await self._save_annotated(img, "move")
                self.previous_screenshot = self._make_screenshot_info(
                    encode_file_base64(file_path=path), path, f"Action: Move to ({x}, {y})"
                )
            self._step += 1
            result = await self._service.move(x, y)
            return self._wrap(result, {"x": x, "y": y})
        except Exception as e:
            logger.error(f"| ❌ move failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="keypress", description="Press specified keys")
    async def keypress(self, keys: List[str], **kwargs) -> Dict[str, Any]:
        try:
            self._step += 1
            result = await self._service.keypress(keys)
            return self._wrap(result, {"keys": keys})
        except Exception as e:
            logger.error(f"| ❌ keypress failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    @environment_manager.action(name="drag", description="Drag mouse along specified path")
    async def drag(self, path: List[List[int]], **kwargs) -> Dict[str, Any]:
        try:
            if self.screenshot:
                img = _b64_to_image(self.screenshot.screenshot)
                img = await self._ss.draw_path(img, path)
                save_path = await self._save_annotated(img, "drag")
                self.previous_screenshot = self._make_screenshot_info(
                    encode_file_base64(file_path=save_path), save_path,
                    f"Action: Drag along {len(path)} points"
                )
            self._step += 1
            result = await self._service.drag(path)
            return self._wrap(result, {"path": path})
        except Exception as e:
            logger.error(f"| ❌ drag failed: {e}")
            return {"success": False, "message": str(e), "extra": {"error": str(e)}}

    # ------------------------------------------------------------------ state

    async def get_state(self, **kwargs) -> Dict[str, Any]:
        try:
            state_data = await self._service.get_state()

            state_text = dedent(f"""
                <info>
                Current URL: {state_data["url"]}
                Current Title: {state_data["title"]}
                Open Tabs: {state_data["tabs"]}
                </info>
            """)

            screenshots = []
            if state_data.get("screenshot"):
                img = _b64_to_image(state_data["screenshot"])
                filename = f"step_{self._step:04d}_state.png"
                path = await self._ss.store_screenshot(img, self._step, filename)
                self.screenshot = self._make_screenshot_info(
                    state_data["screenshot"], path,
                    "Browser state screenshot"
                )
                if not self.previous_screenshot:
                    self.previous_screenshot = self.screenshot
                screenshots = [self.previous_screenshot, self.screenshot]

            self._step += 1

            return {
                "state": state_text,
                "extra": {
                    "step_number": self._step,
                    "headless": self.headless,
                    "viewport": self.viewport,
                    "base_dir": self.base_dir,
                    "screenshots": screenshots,
                    "url": state_data["url"],
                    "title": state_data["title"],
                    "tabs": state_data["tabs"],
                },
            }
        except Exception as e:
            logger.error(f"| ❌ get_state failed: {e}")
            return {"state": "Failed to get browser state", "extra": {"error": str(e)}}
