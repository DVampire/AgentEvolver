"""Computer environment — a full Linux desktop the agent drives with mouse and
keyboard, watched live over noVNC.

A general "computer-use" environment: any GUI app works (a browser for Bilibili,
Telegram, …) with no per-app integration. It is the desktop generalization of the
browser environment — same ECP surface (@action / get_state / live_view), a
heavier backend (a whole desktop instead of one headless page).

Control channel: the ``computer`` sandbox's ``run_command`` runs ``xdotool`` for
input and ``scrot`` for capture inside the container (no CDP, unlike the browser).
"""

import asyncio
import base64
import io
import json
import shlex
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment, EnvironmentView
from agentevolver.logger import logger
from agentevolver.registry import ENVIRONMENT

_BUTTONS = {"left": 1, "middle": 2, "right": 3}

#: Where a capture lands inside the container before it is read back.
_SHOT_PATH = "/tmp/agentevolver_shot.png"


@ENVIRONMENT.register_module(force=True)
class ComputerEnvironment(Environment):
    """A Linux desktop driven with generic mouse/keyboard actions."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    # Class fields, not __init__ parameters. The registry reads a class's identity from
    # `model_fields[...].default` *before* it ever builds an instance — passing these to
    # `super().__init__` left the defaults as PydanticUndefined, so registration died with
    # "env_name: Input should be a valid string" and the environment never existed. It
    # would still appear in the frontend's Machines panel, backed by nothing.
    name: str = Field(default="computer_environment")
    description: str = Field(
        default="A full Linux desktop operated with mouse and keyboard, watchable live "
                "over noVNC."
    )
    metadata: Dict[str, Any] = Field(default={"has_vision": True})
    enable_evolving: bool = Field(default=False)

    def __init__(
        self,
        width: int = 1280,
        height: int = 800,
        provider: str = "docker-linux",
        use_som: bool = True,
        persist_profile: Optional[str] = None,
        sandbox_timeout_minutes: int = 60,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.width = width
        self.height = height
        self.use_som = use_som
        # provider abstraction: docker-linux today; vm-windows / vm-macos are future
        # heavyweight backends exposing the same start_desktop / vnc_ws_url / run surface.
        self.provider = provider
        # A persistent home volume name so logins (WeChat/Telegram QR, cookies) survive
        # across sessions; requires backend volume support (opensandbox). None = ephemeral.
        self.persist_profile = persist_profile
        self.sandbox_timeout_minutes = sandbox_timeout_minutes
        # session_id -> started "computer" sandbox handle (one desktop per session).
        self._sandboxes: Dict[str, Any] = {}
        # Per-session lock: one desktop has one mouse/keyboard — serialize access so
        # concurrent actions (or a double first-acquire) can't fight over it.
        self._locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ session
    @staticmethod
    def _session_id(ctx) -> str:
        return (getattr(ctx, "id", None) or "default") if ctx is not None else "default"

    async def _desktop(self, ctx):
        """Return the started desktop sandbox for this session, creating it once.

        Guarded by a per-session lock so two concurrent actions can't both create
        (or both drive) the one desktop — a desktop is a single exclusive resource.
        """
        sid = self._session_id(ctx)
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:
            sandbox = self._sandboxes.get(sid)
            if sandbox is None:
                from agentevolver.sandbox import sandbox_manager
                acquire_kwargs = {"timeout_minutes": self.sandbox_timeout_minutes}
                if self.persist_profile:
                    # Backend volume support required; carried as a hint for the sandbox.
                    acquire_kwargs["env"] = {"AGENTEVOLVER_PROFILE": self.persist_profile}
                sandbox = await sandbox_manager.acquire("computer", reuse_key=sid, **acquire_kwargs)
                await sandbox.start_desktop(width=self.width, height=self.height)
                self._sandboxes[sid] = sandbox
            return sandbox

    async def close_session(self, session_id: str) -> None:
        """Release this session's desktop container."""
        from agentevolver.sandbox import sandbox_manager
        self._sandboxes.pop(session_id, None)
        await sandbox_manager.release("computer", reuse_key=session_id)

    async def _xdotool(self, ctx, args: str) -> None:
        sandbox = await self._desktop(ctx)
        result = await sandbox.run(f"xdotool {args}")
        if not result.success:
            raise RuntimeError(result.error or f"xdotool {args} failed")

    def _ok(self, message: str, **extra) -> Dict[str, Any]:
        """The same shape `browser_environment` returns — `message` plus an `extra` bag.

        Images do not travel this way. They reach the model through `get_state`, which
        the agent loop turns into image content parts; an action's job is to say what it
        did. Flattening this to put a screenshot in the action result would put a
        megabyte of base64 into the text stream instead.
        """
        return {"success": True, "message": message, "extra": extra}

    def _err(self, e: Exception) -> Dict[str, Any]:
        logger.error(f"| ❌ computer action failed: {type(e).__name__}: {e}")
        return {"success": False, "message": str(e)}

    # ------------------------------------------------------------------ actions
    @environment_manager.action(name="click", description="Click at (x, y). button: left|middle|right.")
    async def click(self, x: int, y: int, button: str = "left", ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            await self._xdotool(ctx, f"mousemove --sync {int(x)} {int(y)} click {_BUTTONS.get(button, 1)}")
            return self._ok(f"clicked ({x},{y}) {button}", x=x, y=y, button=button)
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="double_click", description="Double-click at (x, y).")
    async def double_click(self, x: int, y: int, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            await self._xdotool(ctx, f"mousemove --sync {int(x)} {int(y)} click --repeat 2 --delay 80 1")
            return self._ok(f"double-clicked ({x},{y})", x=x, y=y)
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="move", description="Move the mouse to (x, y).")
    async def move(self, x: int, y: int, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            await self._xdotool(ctx, f"mousemove --sync {int(x)} {int(y)}")
            return self._ok(f"moved to ({x},{y})", x=x, y=y)
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="drag", description="Drag from (x1, y1) to (x2, y2) with the left button.")
    async def drag(self, x1: int, y1: int, x2: int, y2: int, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            await self._xdotool(
                ctx,
                f"mousemove --sync {int(x1)} {int(y1)} mousedown 1 "
                f"mousemove --sync {int(x2)} {int(y2)} mouseup 1",
            )
            return self._ok(f"dragged ({x1},{y1})->({x2},{y2})")
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="scroll", description="Scroll at (x, y). amount>0 scrolls down, <0 up.")
    async def scroll(self, x: int, y: int, amount: int = 3, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            button = 5 if amount >= 0 else 4  # 5=down, 4=up
            await self._xdotool(ctx, f"mousemove --sync {int(x)} {int(y)} click --repeat {abs(int(amount)) or 1} {button}")
            return self._ok(f"scrolled {amount} at ({x},{y})")
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="type", description="Type UTF-8 text at the current focus.")
    async def type(self, text: str, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            await self._xdotool(ctx, f"type --clearmodifiers -- {shlex.quote(str(text))}")
            return self._ok("typed text", length=len(text or ""))
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="keypress", description="Press a key or combo, e.g. 'Return', 'ctrl+c', 'alt+Tab'.")
    async def keypress(self, keys: str, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            # xdotool key names; split on space to allow a sequence of chords.
            combos = " ".join(shlex.quote(k) for k in str(keys).split())
            await self._xdotool(ctx, f"key --clearmodifiers {combos}")
            return self._ok(f"pressed {keys}", keys=keys)
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="open_app", description="Launch a program on the desktop, e.g. 'chromium --no-sandbox https://bilibili.com'.")
    async def open_app(self, command: str, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            sandbox = await self._desktop(ctx)
            # Detach so the launcher returns immediately and the app keeps running.
            result = await sandbox.run(f"setsid sh -c {shlex.quote(str(command))} >/tmp/app.log 2>&1 &")
            if not result.success:
                raise RuntimeError(result.error or "launch failed")
            return self._ok(f"launched: {command}", command=command)
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="wait", description="Wait for the given milliseconds (default 1000).")
    async def wait(self, ms: int = 1000, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            import asyncio
            await asyncio.sleep(max(0, int(ms)) / 1000.0)
            return self._ok(f"waited {ms}ms")
        except Exception as e:
            return self._err(e)

    @environment_manager.action(name="screenshot", description="Capture the desktop and return a base64 PNG.")
    async def screenshot(self, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            b64 = await self._capture(ctx)
            return self._ok("captured", screenshot=b64, mime_type="image/png")
        except Exception as e:
            return self._err(e)

    # ------------------------------------------------------------------ capture / state / view
    async def _capture(self, ctx) -> str:
        """Take a screenshot inside the container and return it base64-encoded.

        The file is the verdict, not the exit code. `scrot` writes the PNG and *then*
        exits non-zero often enough that trusting its status threw away screenshots that
        were sitting on disk — and the failure surfaced as a live view that reported
        success with zero bytes of image, which is worse than an error.
        """
        sandbox = await self._desktop(ctx)
        shot = await sandbox.run(f"scrot -o {_SHOT_PATH}")
        raw = await sandbox.read_bytes(_SHOT_PATH)
        if isinstance(raw, str):
            raw = raw.encode("latin-1", errors="ignore")
        if not raw:
            raise RuntimeError(
                (getattr(shot, "error", None) or "scrot produced no image") if not shot.success
                else "scrot reported success but wrote nothing"
            )
        return base64.b64encode(raw).decode("ascii")

    async def _accessibility(self, ctx) -> List[Dict[str, Any]]:
        """Fetch on-screen elements from the container's AT-SPI tree (may be empty)."""
        sandbox = await self._desktop(ctx)
        try:
            res = await sandbox.run("python3 /usr/local/bin/a11y-dump", timeout=15)
            if not res.success:
                return []
            data = json.loads((getattr(res, "stdout", None) or getattr(res, "output", "") or "").strip() or "{}")
            return data.get("elements", []) if isinstance(data, dict) else []
        except Exception as e:  # noqa: BLE001
            logger.debug(f"| a11y dump failed: {e}")
            return []

    @staticmethod
    def _draw_som(png_b64: str, elements: List[Dict[str, Any]]) -> str:
        """Overlay numbered boxes on the screenshot so the model can click by id."""
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return png_b64
        try:
            img = Image.open(io.BytesIO(base64.b64decode(png_b64))).convert("RGB")
            draw = ImageDraw.Draw(img)
            for idx, el in enumerate(elements):
                x, y, w, h = el.get("x", 0), el.get("y", 0), el.get("w", 0), el.get("h", 0)
                draw.rectangle([x, y, x + w, y + h], outline=(255, 60, 60), width=2)
                draw.rectangle([x, y - 14, x + 22, y], fill=(255, 60, 60))
                draw.text((x + 3, y - 13), str(idx), fill=(255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return png_b64

    async def get_state(self, ctx=None, **kwargs) -> Dict[str, Any]:
        """Desktop state: screenshot + accessibility elements + (optional) SoM overlay.

        Elements carry ``id`` (their index) so the agent can click by id via the
        element's center, rather than guessing raw pixels. When the accessibility
        tree is empty (apps without AT-SPI support), only the screenshot is returned.
        """
        try:
            b64 = await self._capture(ctx)
            elements = await self._accessibility(ctx)
            for idx, el in enumerate(elements):
                el["id"] = idx
                el["center"] = [el.get("x", 0) + el.get("w", 0) // 2, el.get("y", 0) + el.get("h", 0) // 2]
            annotated = self._draw_som(b64, elements) if (self.use_som and elements) else b64
            # `extra.screenshots`, with each entry carrying `screenshot` /
            # `screenshot_path` / `screenshot_description` — the shape
            # `BrowserAgent._get_messages` reads to attach images to the prompt. This used
            # to return them flat, which the loop simply did not look at: the desktop
            # agent ran blind, reasoning over an empty picture of a screen it could not
            # see. It is the same loop for both environments, so it is the same shape.
            shot = SimpleNamespace(
                screenshot=annotated,
                screenshot_path=f"{self._session_id(ctx)}/desktop.png",
                screenshot_description="Current desktop"
                                       + (" (numbered boxes match the element ids)"
                                          if (self.use_som and elements) else ""),
            )
            return {
                "success": True,
                "message": f"{len(elements)} element(s) on a "
                           f"{self.width}x{self.height} desktop",
                "state": self._render_state(elements),
                "extra": {
                    "screenshots": [shot],
                    "screenshot_raw": b64,
                    "elements": elements,
                    "size": {"width": self.width, "height": self.height},
                },
            }
        except Exception as e:
            return self._err(e)

    def _render_state(self, elements: List[Dict[str, Any]]) -> str:
        """A text rendering of the screen, for the prompt's state block.

        The screenshot carries the truth; this is what a model can quote back and what
        appears in a log where an image cannot.
        """
        if not elements:
            return (f"Desktop {self.width}x{self.height}. No accessible elements "
                    f"reported — work from the screenshot and click by coordinate.")
        lines = [f"Desktop {self.width}x{self.height}, {len(elements)} elements:"]
        for el in elements[:40]:
            name = str(el.get("name") or "").strip()[:60]
            lines.append(f"  [{el.get('id')}] {el.get('role', '?')}"
                         + (f" {name!r}" if name else "")
                         + f" at {el.get('center')}")
        if len(elements) > 40:
            lines.append(f"  … {len(elements) - 40} more")
        return "\n".join(lines)

    @environment_manager.action(name="click_element", description="Click a UI element by its id from get_state's element list.")
    async def click_element(self, element_id: int, ctx=None, **kwargs) -> Dict[str, Any]:
        try:
            elements = await self._accessibility(ctx)
            if not (0 <= int(element_id) < len(elements)):
                return {"success": False, "message": f"No element id {element_id}"}
            el = elements[int(element_id)]
            cx, cy = el.get("x", 0) + el.get("w", 0) // 2, el.get("y", 0) + el.get("h", 0) // 2
            await self._xdotool(ctx, f"mousemove --sync {cx} {cy} click 1")
            return self._ok(f"clicked element {element_id} ({el.get('role')} {el.get('name')!r})", element_id=element_id)
        except Exception as e:
            return self._err(e)

    async def live_view(self, ctx=None) -> Optional[EnvironmentView]:
        """Expose the desktop's noVNC socket so the frontend can watch it live."""
        try:
            sandbox = await self._desktop(ctx)
            url = await sandbox.vnc_ws_url()
            if not url:
                return None
            return EnvironmentView(env_name=self.name, type="vnc", url=url, label="Desktop (live)")
        except Exception as e:
            # Logged loudly rather than swallowed: the frontend's only symptom is "this
            # environment has no live view", which reads as "not implemented" and sent
            # the last two hours looking in the wrong place. It was a TypeError.
            logger.warning(f"| ⚠️ computer live_view unavailable: {type(e).__name__}: {e}")
            return None
