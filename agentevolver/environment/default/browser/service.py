"""Playwright-based browser service.

Supports two launch modes:
  - Local (default): launches a Chromium process via Playwright directly.
  - OpenSandbox: spins up an opensandbox/chrome container and connects via CDP.
"""

import asyncio
import base64
import io
import re
import textwrap
import tokenize
from functools import wraps
from typing import Any, Dict, List, Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

from agentevolver.logger import logger
from agentevolver.response.types import Response, ResponseType

# Scans the page for visible interactive elements and collects scroll/focus info.
# Coordinates are CSS pixels relative to the viewport, matching page.mouse coordinates.
_OBSERVE_JS = """
() => {
  const SELECTOR = 'a, button, input, select, textarea, summary, ' +
    '[role=button], [role=link], [role=checkbox], [role=radio], [role=combobox], ' +
    '[role=menuitem], [role=tab], [role=option], [role=switch], [role=searchbox], ' +
    '[onclick], [contenteditable=true]';
  const vw = window.innerWidth, vh = window.innerHeight;
  const out = [];
  let idx = 1;
  for (const el of document.querySelectorAll(SELECTOR)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) === 0) continue;
    const text = (el.innerText || el.value || el.placeholder ||
      el.getAttribute('aria-label') || el.getAttribute('title') || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 80);
    let selector = '';
    if (el.id) selector = '#' + CSS.escape(el.id);
    else if (el.getAttribute('name')) selector = el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    else if (el.getAttribute('aria-label')) selector = el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label') + '"]';
    out.push({
      index: idx++,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      text: text,
      selector: selector,
      x: Math.round(r.left + r.width / 2),
      y: Math.round(r.top + r.height / 2),
      left: Math.round(r.left),
      top: Math.round(r.top),
      width: Math.round(r.width),
      height: Math.round(r.height),
      in_viewport: r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw,
    });
  }
  const ae = document.activeElement;
  return {
    elements: out,
    scroll: {
      x: Math.round(window.scrollX),
      y: Math.round(window.scrollY),
      page_width: Math.round(document.documentElement.scrollWidth),
      page_height: Math.round(document.documentElement.scrollHeight),
      viewport_width: vw,
      viewport_height: vh,
    },
    focus: ae && ae !== document.body
      ? ae.tagName.toLowerCase() + (ae.id ? '#' + ae.id : '') + (ae.getAttribute('name') ? '[name=' + ae.getAttribute('name') + ']' : '')
      : 'none',
    iframes: document.querySelectorAll('iframe').length,
  };
}
"""


_JAVASCRIPT_COMMAND_PATTERNS = (
    re.compile(r"\b(?:const|let|var)\s+[A-Za-z_$]"),
    re.compile(r"=>"),
    re.compile(r"\.slice\s*\("),
    re.compile(r"\.get_by_(?:role|text|label)\([^\n]*,\s*\{"),
)


def _javascript_command_hint(code: str) -> bool:
    """Check Python tokens, not JavaScript inside evaluate strings or comments."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code or "").readline)
        source = tokenize.untokenize([
            token if token.type not in (tokenize.STRING, tokenize.COMMENT) else token._replace(string="")
            for token in tokens
        ])
    except (tokenize.TokenError, IndentationError):
        return False  # The compiler supplies the precise syntax error below.
    return any(pattern.search(source) for pattern in _JAVASCRIPT_COMMAND_PATTERNS)


# Returns page HTML with non-content nodes stripped (scripts, styles, svg, hidden elements).
_CLEAN_HTML_JS = """
() => {
  const clone = document.documentElement.cloneNode(true);
  clone.querySelectorAll('script, style, svg, noscript, link, meta, template').forEach(e => e.remove());
  return clone.outerHTML.replace(/\\n\\s*\\n/g, '\\n');
}
"""


class BrowserService:
    """Browser service backed directly by Playwright."""

    def __init__(
        self,
        headless: bool = True,
        viewport: Dict[str, int] = None,
        use_sandbox: bool = False,
        sandbox_domain: Optional[str] = None,  # None -> resolved via the port manager
        sandbox_api_key: Optional[str] = None,
        sandbox_image: str = "opensandbox/chrome:latest",
        sandbox_timeout_minutes: int = 30,
        vnc: bool = False,
        action_timeout: float = 5.0,
    ):
        # VNC live view needs a headful browser in the chrome-vnc sandbox.
        self.vnc = vnc
        self.action_timeout = max(0.1, float(action_timeout))
        if vnc:
            use_sandbox = True
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
        # Per-session isolation: each session_id gets its own BrowserContext + Page
        # (independent cookies/storage) inside the one shared browser process.
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._sandbox = None  # opensandbox Sandbox instance

    async def start(self):
        """Launch the browser. Pages are created lazily, one per session."""
        try:
            self._playwright = await async_playwright().start()

            if self.use_sandbox:
                await self._start_sandbox()
            else:
                await self._start_local()

            logger.info("| 🌐 BrowserService started")
        except Exception as e:
            logger.error(f"| ❌ Failed to start browser: {e}")
            # A failed start must not leak partial state: the acquired peer
            # container especially (an orphaned chrome sandbox breaks every
            # later boot), but also the Playwright driver process.
            await self._cleanup_failed_start()
            raise

    async def _cleanup_failed_start(self) -> None:
        async def _guard(label: str, coro, timeout: float = 20.0):
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except Exception as cleanup_error:  # noqa: BLE001 — best-effort teardown
                logger.warning(f"| ⚠️ Browser start-failure cleanup ({label}): {cleanup_error}")

        if self._browser:
            await _guard("browser.close", self._browser.close(), timeout=10.0)
        if self._sandbox:
            await _guard("sandbox.destroy", self._sandbox.destroy())
        if self._playwright:
            await _guard("playwright.stop", self._playwright.stop(), timeout=10.0)
        self._browser = None
        self._sandbox = None
        self._playwright = None

    async def _start_local(self):
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        logger.info("| 🖥️  Local Chromium launched")

    async def _start_sandbox(self):
        """Connect to a Chrome container via the sandbox subsystem.

        The PlaywrightSandbox (``agentevolver.sandbox``) owns the opensandbox-server
        daemon lifecycle, container creation, and the CDP proxy ws-url rewrite.
        """
        from agentevolver.sandbox import sandbox_manager

        # chrome-vnc runs headful Chrome + noVNC (for the live view); plain
        # playwright is headless. The chrome-vnc sandbox supplies its own image.
        sandbox_kind = "chrome-vnc" if self.vnc else "playwright"
        sandbox_image = None if self.vnc else self.sandbox_image
        self._sandbox = await sandbox_manager.acquire(
            sandbox_kind,
            image=sandbox_image,
            domain=self.sandbox_domain,
            api_key=self.sandbox_api_key,
            timeout_minutes=self.sandbox_timeout_minutes,
        )
        ws_url = await self._sandbox.cdp_ws_url()
        self._browser = await self._playwright.chromium.connect_over_cdp(ws_url)

    async def vnc_ws_url(self) -> Optional[str]:
        """The websockify WS URL for the live view, or None when VNC isn't active."""
        sandbox = self._sandbox
        if not self.vnc or sandbox is None or not hasattr(sandbox, "vnc_ws_url"):
            return None
        try:
            return await sandbox.vnc_ws_url()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not resolve VNC url: {e}")
            return None

    async def stop(self):
        """Close all sessions and the browser.

        Each teardown step is time-boxed and isolated: a CDP close or a peer-container
        destroy that hangs (e.g. the chrome peer being torn down underneath the CDP
        connection) must not block the others — in particular the peer sandbox must
        always be destroyed so it can't leak, and the process can exit cleanly (which
        is what lets the launcher chown outputs back to the host user).
        """

        async def _guard(label: str, coro, timeout: float = 15.0):
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"| ⚠️ Browser teardown step timed out: {label}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"| ⚠️ Browser teardown step failed ({label}): {e}")

        for sid in list(self._sessions.keys()):
            await _guard(f"close_session {sid}", self.close_session(sid), timeout=5.0)
        if self._browser:
            await _guard("browser.close", self._browser.close(), timeout=10.0)
        if self._playwright:
            await _guard("playwright.stop", self._playwright.stop(), timeout=10.0)
        if self._sandbox:
            await _guard("sandbox.destroy", self._sandbox.destroy(), timeout=20.0)
        self._sessions.clear()
        self._browser = None
        self._playwright = None
        self._sandbox = None
        logger.info("| 🛑 BrowserService stopped")

    # ------------------------------------------------------------------ session management

    async def _page_for(self, session_id: str = "default") -> Optional[Page]:
        """Return the Page for a session, lazily creating an isolated context+page."""
        if not self._browser:
            return None
        sess = self._sessions.get(session_id)
        if sess is None:
            try:
                context = await self._browser.new_context(viewport=self.viewport)
            except Exception as error:
                # Sharing a CDP default context would mix cookies, localStorage and
                # navigation across concurrent user Agents. Isolation is part of the
                # BrowserEnvironment contract, so fail closed when a backend cannot
                # provide it instead of silently turning three users into one browser.
                raise RuntimeError(
                    "browser backend cannot create an isolated BrowserContext for "
                    f"session {session_id!r}"
                ) from error
            page = await context.new_page()
            page.set_default_timeout(self.action_timeout * 1000)
            page.set_default_navigation_timeout(10000)
            sess = {
                "context": context,
                "page": page,
                "owns_context": True,
                "diagnostics": {},
                "diagnostic_seq": 0,
            }
            self._sessions[session_id] = sess
            self._attach_diagnostics(page, session_id)
            self._attach_dialogs(page, session_id)
            try:
                await page.goto("about:blank")
            except Exception:
                pass
            logger.info(f"| 🪟 Browser session created: {session_id}")
        return sess["page"]

    async def close_session(self, session_id: str = "default") -> None:
        """Close a session's page and context (if we created it)."""
        sess = self._sessions.pop(session_id, None)
        if not sess:
            return
        try:
            await sess["page"].close()
            if sess.get("owns_context") and sess.get("context"):
                await sess["context"].close()
            logger.info(f"| 🧹 Browser session closed: {session_id}")
        except Exception as e:
            logger.warning(f"| ⚠️ Error closing session {session_id}: {e}")

    # ------------------------------------------------------------------ helpers

    async def _screenshot_b64(self, page: Page) -> str:
        """Return a base64-encoded PNG screenshot of the given page."""
        data = await page.screenshot(type="png", timeout=5000)
        return base64.b64encode(data).decode("utf-8")

    def _attach_dialogs(self, page: Page, session_id: str) -> None:
        sess = self._sessions[session_id]
        sess["dialog"] = None
        sess["dialog_open"] = asyncio.Event()

        def opened(dialog):
            sess["dialog"] = dialog
            sess["dialog_open"].set()
            self._record_diagnostic(session_id, "dialog", dialog.message, page.url)
            # Keep native async callbacks compatible: whichever public method
            # resolves this dialog also clears our observation of it.
            def track(method):
                @wraps(method)
                async def resolve(*args, **kwargs):
                    result = await method(*args, **kwargs)
                    if sess["dialog"] is dialog:
                        sess["dialog"] = None
                        sess["dialog_open"].clear()
                    return result
                return resolve
            dialog.accept = track(dialog.accept)
            dialog.dismiss = track(dialog.dismiss)

        page.on("dialog", opened)

    def pending_dialog(self, session_id: str = "default") -> Optional[Dict[str, Any]]:
        dialog = (self._sessions.get(session_id) or {}).get("dialog")
        if dialog is None:
            return None
        return {"type": dialog.type, "message": dialog.message, "default_value": dialog.default_value}

    async def _run_action(self, operation, session_id: str, timeout: float = 10.0):
        """Bound browser I/O and surface modal blocking without accepting consent."""
        task = asyncio.ensure_future(operation)
        event = (self._sessions.get(session_id) or {}).get("dialog_open")
        waiter = asyncio.create_task(event.wait()) if event is not None else None
        try:
            if self.pending_dialog(session_id):
                raise RuntimeError("Dialog pending: use handle_dialog to accept or dismiss it; do not repeat the triggering action.")
            done, _ = await asyncio.wait([task, *([waiter] if waiter else [])], timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                # Give a correctly awaited native dialog callback a chance to finish.
                await asyncio.sleep(0.05)
                if self.pending_dialog(session_id):
                    raise RuntimeError("Dialog pending: use handle_dialog to accept or dismiss it; do not repeat the triggering action.")
                return await asyncio.wait_for(task, timeout=timeout)
            if task in done:
                return task.result()
            raise asyncio.TimeoutError(f"Browser operation exceeded {timeout}s")
        finally:
            for pending in (task, waiter):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*[t for t in (task, waiter) if t is not None], return_exceptions=True)

    async def handle_dialog(self, accept: bool, prompt_text: str = "", session_id: str = "default") -> Response:
        dialog = (self._sessions.get(session_id) or {}).get("dialog")
        if dialog is None:
            return Response(type=ResponseType.ENVIRONMENT, success=False, message="No pending dialog")
        try:
            operation = dialog.accept(prompt_text) if accept and dialog.type == "prompt" else (
                dialog.accept() if accept else dialog.dismiss())
            await asyncio.wait_for(operation, timeout=5)
            return Response(type=ResponseType.ENVIRONMENT, success=True,
                            message="Dialog accepted" if accept else "Dialog dismissed")
        except Exception as error:
            return Response(type=ResponseType.ENVIRONMENT, success=False, message=str(error))

    def _tabs(self, page: Page) -> List[str]:
        """Return URLs of all open pages in the page's context."""
        return [p.url for p in page.context.pages]

    def _record_diagnostic(
        self,
        session_id: str,
        type: str,
        message: str,
        url: str = "",
    ) -> None:
        """Record browser-native failures without modifying the inspected page."""
        sess = self._sessions.get(session_id)
        if sess is None:
            return
        text = str(message or "(no message)")
        source = str(url or "")
        key = (type, text, source)
        sess["diagnostic_seq"] += 1
        entry = sess["diagnostics"].setdefault(
            key,
            {
                "type": type,
                "message": text,
                "url": source,
                "count": 0,
                "first_seq": sess["diagnostic_seq"],
                "last_seq": sess["diagnostic_seq"],
            },
        )
        entry["count"] += 1
        entry["last_seq"] = sess["diagnostic_seq"]

    def _attach_diagnostics(self, page: Page, session_id: str) -> None:
        def on_console(message) -> None:
            level = str(getattr(message, "type", "") or "").lower()
            if level not in {"warning", "error"}:
                return
            location = getattr(message, "location", None) or {}
            self._record_diagnostic(
                session_id,
                f"console.{level}",
                getattr(message, "text", ""),
                location.get("url", "") if isinstance(location, dict) else "",
            )

        def on_page_error(error) -> None:
            self._record_diagnostic(session_id, "pageerror", str(error), page.url)

        def on_request_failed(request) -> None:
            failure = getattr(request, "failure", None)
            detail = failure() if callable(failure) else failure
            method = getattr(request, "method", "")
            self._record_diagnostic(
                session_id,
                "requestfailed",
                f"{method} {detail or 'request failed'}".strip(),
                getattr(request, "url", ""),
            )

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("requestfailed", on_request_failed)

    def diagnostics(self, session_id: str = "default") -> Dict[str, Any]:
        sess = self._sessions.get(session_id) or {}
        entries = [dict(item) for item in (sess.get("diagnostics") or {}).values()]
        entries.sort(key=lambda item: item["first_seq"])
        counts: Dict[str, int] = {}
        for item in entries:
            counts[item["type"]] = counts.get(item["type"], 0) + item["count"]
        return {
            "sequence": int(sess.get("diagnostic_seq") or 0),
            "total": sum(counts.values()),
            "counts": counts,
            "events": entries,
        }

    def _unavailable(self, action: str) -> Response:
        return Response(
            type=ResponseType.ENVIRONMENT,
            success=False,
            message="Browser not available",
            data={"error": "Browser not available", "action": action},
        )

    # ------------------------------------------------------------------ actions

    async def goto(
        self, url: str, wait_until: str = "domcontentloaded", session_id: str = "default"
    ) -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("goto")
        try:
            if not url.startswith(("http://", "https://", "file://", "about:")):
                url = "https://" + url
            await self._run_action(page.goto(url, wait_until=wait_until, timeout=10000), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Navigated to {page.url}",
                data={"url": page.url},
            )
        except Exception as e:
            logger.error(f"| ❌ goto failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=f"Failed to navigate to {url}: {e}",
                data={"error": str(e)},
            )

    async def search(self, query: str, num_results: int = 5) -> Response:
        """Web search via Firecrawl (server-side crawl, bypasses local IP blocks).

        Returns title/url/description per result so the agent can pick a link and
        `goto` it. Does not touch the page — it's a pure API call.
        """
        from agentevolver.utils import hvac_client

        api_key = hvac_client.get("FIRECRAWL_API_KEY") or ""
        api_base = hvac_client.get("FIRECRAWL_API_BASE") or "https://api.firecrawl.dev/v2"
        if not api_key:
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message="FIRECRAWL_API_KEY not set",
                data={"error": "no_api_key"},
            )
        if not query or not query.strip():
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message="Search query cannot be empty",
                data={"error": "empty_query"},
            )

        import httpx

        try:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"query": query.strip(), "limit": num_results}
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{api_base}/search", json=payload, headers=headers, timeout=httpx.Timeout(60.0)
                )
                resp.raise_for_status()
                data = resp.json()

            raw = data.get("data", {})
            web = raw.get("web", []) if isinstance(raw, dict) else (raw or [])
            results = [
                {
                    "position": i + 1,
                    "title": it.get("title", "") or it.get("metadata", {}).get("title", ""),
                    "url": it.get("url", ""),
                    "description": it.get("description", "")
                    or it.get("metadata", {}).get("description", ""),
                }
                for i, it in enumerate(web[:num_results])
            ]
            if not results:
                return Response(
                    type=ResponseType.ENVIRONMENT,
                    success=True,
                    message=f"No search results for: {query}",
                    data={"query": query, "results": []},
                )

            lines = [
                f"[{r['position']}] {r['title']}\n    {r['url']}\n    {r['description']}"
                for r in results
            ]
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Search results for '{query}':\n" + "\n".join(lines),
                data={"query": query, "results": results},
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"| ❌ search HTTP error: {e.response.status_code}")
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=f"Search failed: HTTP {e.response.status_code} — {e.response.text}",
                data={"error": str(e)},
            )
        except Exception as e:
            logger.error(f"| ❌ search failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=f"Search failed: {e}",
                data={"error": str(e)},
            )

    async def click(
        self, x: int, y: int, button: str = "left", session_id: str = "default"
    ) -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("click")
        try:
            await self._run_action(page.mouse.click(x, y, button=button), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Clicked at ({x}, {y}) with {button} button",
                data={"x": x, "y": y, "button": button},
            )
        except Exception as e:
            logger.error(f"| ❌ click failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def double_click(self, x: int, y: int, session_id: str = "default") -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("double_click")
        try:
            await self._run_action(page.mouse.dblclick(x, y), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Double-clicked at ({x}, {y})",
                data={"x": x, "y": y},
            )
        except Exception as e:
            logger.error(f"| ❌ double_click failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def scroll(
        self, x: int, y: int, scroll_x: int, scroll_y: int, session_id: str = "default"
    ) -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("scroll")
        try:
            await self._run_action(page.mouse.move(x, y), session_id)
            await self._run_action(page.mouse.wheel(scroll_x, scroll_y), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Scrolled at ({x}, {y}) by ({scroll_x}, {scroll_y})",
                data={"x": x, "y": y, "scroll_x": scroll_x, "scroll_y": scroll_y},
            )
        except Exception as e:
            logger.error(f"| ❌ scroll failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def type(self, text: str, session_id: str = "default") -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("type")
        try:
            await self._run_action(page.keyboard.type(text), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Typed: {text}",
                data={"text": text},
            )
        except Exception as e:
            logger.error(f"| ❌ type failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def wait(self, ms: int, session_id: str = "default") -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("wait")
        try:
            await asyncio.sleep(ms / 1000.0)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Waited {ms}ms",
                data={"ms": ms},
            )
        except Exception as e:
            logger.error(f"| ❌ wait failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def move(self, x: int, y: int, session_id: str = "default") -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("move")
        try:
            await self._run_action(page.mouse.move(x, y), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Moved to ({x}, {y})",
                data={"x": x, "y": y},
            )
        except Exception as e:
            logger.error(f"| ❌ move failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def keypress(self, keys: List[str], session_id: str = "default") -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("keypress")
        try:
            if not keys or any(not isinstance(key, str) or not key.strip() for key in keys):
                raise ValueError("keys must be a non-empty list of key names")
            aliases = {"esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "Space",
                       "enter": "Enter", "return": "Enter", "ctrl": "Control", "control": "Control",
                       "shift": "Shift", "alt": "Alt", "meta": "Meta", "cmd": "Meta",
                       "backspace": "Backspace", "delete": "Delete", "home": "Home", "end": "End",
                       "pageup": "PageUp", "pagedown": "PageDown",
                       **{key.lower(): key for key in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight")}}
            normalized = ["+".join(aliases.get(part.lower(), part) for part in key.split("+")) for key in keys]
            # A modifier list denotes one chord; otherwise entries are successive presses.
            chords = ["+".join(normalized)] if any(k in {"Control", "Shift", "Alt", "Meta"} for k in normalized) else normalized
            for chord in chords:
                await self._run_action(page.keyboard.press(chord), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Pressed {keys}",
                data={"keys": keys},
            )
        except Exception as e:
            logger.error(f"| ❌ keypress failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def drag(self, path: List[List[int]], session_id: str = "default") -> Response:
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("drag")
        try:
            if len(path) < 2:
                raise ValueError("Drag path must have at least 2 points")
            start = path[0]
            await self._run_action(page.mouse.move(start[0], start[1]), session_id)
            await self._run_action(page.mouse.down(), session_id)
            for point in path[1:]:
                await self._run_action(page.mouse.move(point[0], point[1]), session_id)
            await self._run_action(page.mouse.up(), session_id)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Dragged along {len(path)} points",
                data={"path": path},
            )
        except Exception as e:
            logger.error(f"| ❌ drag failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT, success=False, message=str(e), data={"error": str(e)}
            )

    async def command(
        self, code: str, timeout: float = 30.0, session_id: str = "default"
    ) -> Response:
        """Run a Playwright Python snippet with `page` and `context` in scope.

        The code is wrapped into an async function, so it may use `await`
        directly and `return` a value back to the caller.
        """
        page = await self._page_for(session_id)
        if not page:
            return self._unavailable("command")
        if _javascript_command_hint(code):
            message = (
                "Browser command accepts async Playwright Python, not JavaScript. "
                "Use Python keyword arguments and slicing (for example "
                "`page.get_by_role('button', name='Save')` and `text[:80]`). "
                "Run page JavaScript only through `await page.evaluate('...')`."
            )
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=message,
                data={"error": "wrong_command_language"},
            )
        task: Optional[asyncio.Task] = None
        try:
            src = "async def __cmd__(page, context):\n" + textwrap.indent(code, "    ")
            ns: Dict[str, Any] = {}
            exec(src, ns)
            task = asyncio.create_task(ns["__cmd__"](page, page.context))
            # The command has a total budget; each locator has a shorter page default.
            result = await self._run_action(task, session_id, timeout=timeout + 1.0)
            result_repr = repr(result)
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=True,
                message=f"Command executed. Return value: {result_repr}",
                data={"result": result_repr},
            )
        except asyncio.TimeoutError:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            logger.error(f"| ❌ command timed out after {timeout}s")
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=f"Command timed out after {timeout}s. Locators auto-wait up to {self.action_timeout}s by default; "
                f"pass a shorter timeout in the code, e.g. page.locator(...).click(timeout=5000).",
                data={"error": "command_timeout"},
            )
        except Exception as e:
            logger.error(f"| ❌ command failed: {e}")
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=f"Command failed: {e}",
                data={"error": str(e)},
            )

    async def observe(self, page: Page) -> Dict[str, Any]:
        """Scan the page for interactive elements, scroll position, and focus.

        Raises on page errors so get_state's retry loop can handle navigation races.
        """
        return await page.evaluate(_OBSERVE_JS)

    async def get_html(self, page: Page, max_chars: Optional[int] = None) -> str:
        """Return cleaned page HTML (scripts/styles/svg stripped).

        Full HTML by default; pass a positive max_chars to cap it.
        """
        try:
            html = await page.evaluate(_CLEAN_HTML_JS)
            if max_chars and len(html) > max_chars:
                html = (
                    html[:max_chars]
                    + f"\n<!-- ... truncated, {len(html) - max_chars} more chars -->"
                )
            return html
        except Exception as e:
            logger.error(f"| ❌ get_html failed: {e}")
            return ""

    async def get_state(
        self, include_elements: bool = True, include_html: bool = False, session_id: str = "default"
    ) -> Dict[str, Any]:
        """Return current page state for a session including a base64 screenshot."""
        state = {
            "url": None,
            "title": None,
            "tabs": [],
            "screenshot": None,
            "elements": [],
            "scroll": {},
            "focus": "none",
            "iframes": 0,
            "html": "",
            "diagnostics": self.diagnostics(session_id),
            "dialog": self.pending_dialog(session_id),
            "errors": [],
        }
        page = await self._page_for(session_id)
        if not page:
            return state
        state.update(url=page.url, tabs=self._tabs(page))
        if state["dialog"]:
            state["errors"].append("Dialog pending: use handle_dialog; screenshots and navigation are blocked until it is resolved.")
            return state
        # Observing right after an action may race a navigation it triggered:
        # wait_for_load_state returns immediately on the old document, then
        # title/evaluate die with "Execution context was destroyed". Retry.
        last_error = None
        for attempt in range(3):
            try:
                try:
                    await self._run_action(page.wait_for_load_state("domcontentloaded", timeout=2000), session_id, 2.5)
                except Exception:
                    pass
                state.update(url=page.url, tabs=self._tabs(page))
                state["title"] = await self._run_action(page.title(), session_id, 3)
                if include_elements:
                    observed = await self._run_action(self.observe(page), session_id, 3)
                    state.update(
                        {
                            k: observed.get(k, state[k])
                            for k in ("elements", "scroll", "focus", "iframes")
                        }
                    )
                if include_html:
                    state["html"] = await self._run_action(self.get_html(page), session_id, 3)
                # DOM evidence remains usable even if rendering/screenshot fails.
                state["screenshot"] = await self._run_action(self._screenshot_b64(page), session_id, 6)
                state["diagnostics"] = self.diagnostics(session_id)
                return state
            except Exception as e:
                last_error = e
                if "Execution context was destroyed" in str(e):
                    state.update(title=None, elements=[], scroll={}, focus="none", html="", screenshot=None)
                    await asyncio.sleep(0.7)
                    continue
                break
        logger.error(f"| ❌ get_state failed: {last_error}")
        state["errors"].append(str(last_error) or type(last_error).__name__)
        state.update(url=page.url, tabs=self._tabs(page), dialog=self.pending_dialog(session_id),
                     diagnostics=self.diagnostics(session_id))
        return state
