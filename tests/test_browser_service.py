"""Local browser regressions; no models, deployed websites, or external network."""
import time

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from agentevolver.environment.default.browser.service import BrowserService


@pytest.mark.asyncio
async def test_missing_search_credentials_remove_registered_action(monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from agentevolver.environment.context import EnvironmentContextManager
    from agentevolver.environment.default.browser.environment import BrowserEnvironment
    from agentevolver.environment.types import EnvironmentConfig, ActionConfig

    monkeypatch.setattr("agentevolver.utils.hvac_client.get", lambda name: "")
    monkeypatch.setattr(BrowserService, "start", AsyncMock())
    monkeypatch.setattr("agentevolver.environment.context.permission_manager", SimpleNamespace(register=lambda **kw: None))
    cfg = EnvironmentConfig(name="browser_environment", description="test", rules="test", cls=BrowserEnvironment, actions={
        name: ActionConfig(env_name="browser_environment", name=name, description=name)
        for name in ("search", "command")
    })
    built = await EnvironmentContextManager().build(cfg)
    assert "search" not in built.actions and "command" in built.actions
    assert "search" not in built.instance.actions


@pytest.mark.asyncio
async def test_keypress_aliases_chords_and_sequences(service):
    page = await service._page_for("keys")
    await page.set_content('<input id="a"><input id="b"><button>Go</button>')
    await page.locator("#a").fill("hello")
    assert (await service.keypress(["CTRL", "a"], session_id="keys")).success
    assert (await service.keypress(["BACKSPACE"], session_id="keys")).success
    assert await page.locator("#a").input_value() == ""
    assert (await service.keypress(["TAB", "TAB"], session_id="keys")).success
    assert await page.evaluate("document.activeElement.tagName") == "BUTTON"
    assert (await service.keypress(["ESC", "ESCAPE", "SPACE", "ARROWDOWN"], session_id="keys")).success
    assert not (await service.keypress([], session_id="keys")).success


@pytest.mark.asyncio
async def test_command_allows_javascript_inside_python_strings(service):
    page = await service._page_for("one")
    await page.set_content('<title>Echo</title><button>Save</button>')
    result = await service.command(
        'return await page.evaluate("() => { const x = document.title; return x.slice(0, 4); }")',
        session_id="one",
    )
    assert result.success and "Echo" in result.message
    result = await service.command(
        'return await page.locator("button").evaluate_all("els => els.map(e => e.textContent)")',
        session_id="one",
    )
    assert result.success and "Save" in result.message


@pytest.mark.asyncio
async def test_missing_locator_has_short_budget_and_does_not_break_session(service):
    page = await service._page_for("one")
    page.set_default_timeout(100)
    started = time.monotonic()
    result = await service.command('await page.locator("#missing").click()', session_id="one")
    assert not result.success and "Timeout" in result.message
    assert time.monotonic() - started < 2
    assert (await service.command('return page.url', session_id="one")).success


@pytest_asyncio.fixture
async def service():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        svc = BrowserService()
        svc._browser = browser
        try:
            yield svc
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_modal_is_explicit_fast_and_session_isolated(service):
    page = await service._page_for("one")
    await page.set_content('<title>Preview</title><button onclick="window.answer=confirm(\'Commit?\')">Commit</button>')
    other = await service._page_for("two")
    await other.set_content('<title>Other user</title>Ready')
    response = await service.command("await page.get_by_role('button').click()", session_id="one")
    assert not response.success
    assert "handle_dialog" in response.message
    start = time.monotonic()
    state = await service.get_state(session_id="one")
    assert time.monotonic() - start < 1
    assert state["dialog"]["message"] == "Commit?"
    assert state["url"] == "about:blank"
    assert state["screenshot"] is None
    assert state["errors"]
    assert not (await service.goto("about:blank", session_id="one")).success
    assert (await service.get_state(session_id="two"))["screenshot"]
    assert (await service.handle_dialog(False, session_id="one")).success
    assert await page.evaluate("window.answer") is False
    recovered = await service.get_state(session_id="one")
    assert recovered["screenshot"] and recovered["dialog"] is None
    assert not recovered["errors"]


@pytest.mark.asyncio
async def test_screenshot_failure_preserves_dom_and_error(service, monkeypatch):
    page = await service._page_for("one")
    await page.set_content('<title>Still here</title><button id="save">Save</button>')

    async def broken(_page):
        raise TimeoutError("screenshot rendering timed out")

    monkeypatch.setattr(service, "_screenshot_b64", broken)
    state = await service.get_state(session_id="one")
    assert state["title"] == "Still here"
    assert state["elements"][0]["text"] == "Save"
    assert state["screenshot"] is None
    assert "screenshot rendering timed out" in state["errors"][0]


@pytest.mark.asyncio
async def test_native_dialog_callback_and_prompt_resolution(service):
    page = await service._page_for("one")
    await page.set_content('<button onclick="alert(\'Hello\')">Alert</button>')
    result = await service.command(
        "async def resolve(dialog):\n    await dialog.dismiss()\n"
        "page.once('dialog', resolve)\nawait page.get_by_role('button').click()", session_id="one")
    assert result.success
    assert service.pending_dialog("one") is None
    await page.set_content('<button onclick="window.answer=prompt(\'Your name?\',\'Guest\')">Name</button>')
    assert not (await service.command("await page.get_by_role('button').click()", session_id="one")).success
    assert service.pending_dialog("one")["default_value"] == "Guest"
    assert (await service.handle_dialog(True, "Lyra", session_id="one")).success
    assert await page.evaluate("window.answer") == "Lyra"


@pytest.mark.asyncio
async def test_unawaited_legacy_callback_does_not_strand_browser(service):
    page = await service._page_for("one")
    await page.set_content('<button onclick="window.answer=confirm(\'Commit?\')">Commit</button>')
    # The exact bad callback from ECHO must become an explicit pending dialog,
    # not a chain of screenshot/navigation timeouts or an automatic acceptance.
    with pytest.warns(RuntimeWarning, match="never awaited"):
        result = await service.command(
            "def handle(dialog):\n    dialog.accept()\n"
            "page.once('dialog', handle)\nawait page.get_by_role('button').click()", session_id="one")
    assert not result.success
    assert service.pending_dialog("one")["type"] == "confirm"
    assert (await service.handle_dialog(False, session_id="one")).success
    assert await page.evaluate("window.answer") is False
    assert (await service.goto("about:blank", session_id="one")).success
    assert (await service.get_state(session_id="one"))["screenshot"]
