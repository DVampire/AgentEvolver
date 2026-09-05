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
async def test_three_users_navigate_persist_and_capture_independently(service):
    import asyncio
    import json

    html = """<title>Local game preview</title><input id="name"><button onclick="
localStorage.setItem('name', document.querySelector('input').value);
document.cookie='resident='+document.querySelector('input').value">Save</button>
<a href="/next">Next</a><script>
document.querySelector('input').value=localStorage.getItem('name')||'';
</script>"""
    async def participant(name):
        page = await service._page_for(name)
        async def local_page(route):
            await route.fulfill(status=200, content_type="text/html", body=html)
        await page.route("**/*", local_page)
        assert (await service.goto("http://local-game.test/start", session_id=name)).success
        result = await service.command(
            f"await page.locator('#name').fill({json.dumps(name)})\n"
            "await page.get_by_role('button', name='Save').click()\n"
            "await page.get_by_role('link', name='Next').click()", session_id=name)
        assert result.success, result.message
        assert page.url.endswith("/next")
        assert await page.locator("#name").input_value() == name
        assert await page.evaluate("document.cookie") == f"resident={name}"
        state = await service.get_state(session_id=name)
        assert state["screenshot"] and not state["errors"]
        return page.context
    contexts = await asyncio.gather(*(participant(name) for name in ("Ada", "Bo", "Cy")))
    assert len({id(context) for context in contexts}) == 3
    await service.close_session("Ada")
    assert "Ada" not in service._sessions
    assert (await service.get_state(session_id="Bo"))["screenshot"]
    assert (await service.get_state(session_id="Cy"))["screenshot"]


@pytest.mark.asyncio
async def test_browser_close_failure_retains_context_for_retry():
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    svc = BrowserService()
    context = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("transport failure")))
    svc._sessions["retry"] = {"context": context, "owns_context": True}
    with pytest.raises(RuntimeError, match="transport failure"):
        await svc.close_session("retry")
    assert svc._sessions["retry"]["context"] is context
    context.close = AsyncMock()
    await svc.close_session("retry")
    assert "retry" not in svc._sessions


@pytest.mark.asyncio
async def test_browser_storage_recovers_but_does_not_replay_navigation(service, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_checkpoint_path", lambda sid: tmp_path / f"{sid}.json")
    page = await service._page_for("one")
    async def local_page(route):
        await route.fulfill(status=200, content_type="text/html", body="<title>Recovery</title>")
    await page.route("**/*", local_page)
    await page.goto("http://local-game.test/game")
    await page.evaluate("localStorage.setItem('player', 'Ada'); document.cookie='player=Ada'")
    await service.checkpoint("one")
    # Abrupt loss: do not call close_session (which writes a final checkpoint).
    await page.context.close()
    service._sessions.clear()
    recovered = await service._page_for("one")
    assert recovered.url == "about:blank"
    assert any(cookie["value"] == "Ada" for cookie in await recovered.context.cookies())
    await recovered.route("**/*", local_page)
    await recovered.goto("http://local-game.test/game")
    assert await recovered.evaluate("localStorage.getItem('player')") == "Ada"
    other = await service._page_for("two")
    assert not await other.context.cookies()


@pytest.mark.asyncio
async def test_browser_agent_observe_model_action_error_and_recovery(service, tmp_path, monkeypatch):
    """Real BrowserAgent loop + Chromium, deterministic model transport (no API bill)."""
    import asyncio
    from types import SimpleNamespace
    from agentevolver.agent.actor.browser_agent import BrowserAgent
    from agentevolver.agent.loop.router import ToolRouter
    from agentevolver.agent.loop.decision import ActionResult
    from agentevolver.environment.default.browser.environment import BrowserEnvironment
    from agentevolver.environment import environment_manager
    from agentevolver.message.types import SystemMessage, ToolMessage
    from agentevolver.model.types import ToolCallComplete, TextDelta, StreamDone
    from agentevolver.model import model_manager
    from agentevolver.runtime.kernel import Kernel

    env = BrowserEnvironment(base_dir=str(tmp_path), use_som=False)
    env._service = service
    ctx = SimpleNamespace(id="browser-e2e", extra={})
    page = await service._page_for(ctx.id)
    page.set_default_timeout(100)
    await page.set_content('<input id="name"><button onclick="document.querySelector(\'p\').textContent=\'Saved: \'+document.querySelector(\'input\').value">Save</button><p>Ready</p>')

    class Router(ToolRouter):
        async def schemas(self, agent, ctx):
            return [SimpleNamespace(name="command", metadata={})], {"command": ("environment", "browser_environment", "command")}

        async def invoke(self, call, *, ctx, **kwargs):
            result = await env.command(**call.args, ctx=ctx)
            return ActionResult(call=call, output=result["message"],
                                error="" if result["success"] else result["message"])

    async def system(self, ctx):
        return [SystemMessage(content="Use the visible page and finish after saving the name.")]

    async def state(name, *, ctx):
        return await env.get_state(ctx=ctx)

    async def environment(name):
        return env

    observed = []
    async def stream(**kwargs):
        messages = kwargs["input"]["messages"]
        assert any(getattr(part, "type", "") == "image_url" for message in messages
                   if isinstance(message.content, list) for part in message.content)
        step = len(observed)
        observed.append(messages)
        if step == 0:
            yield ToolCallComplete(0, "missing", "command", {"code": "await page.locator('#missing').click()"})
            yield StreamDone("tool_use")
        elif step == 1:
            assert any(isinstance(m, ToolMessage) and "Timeout" in str(m.content) for m in messages)
            yield ToolCallComplete(0, "save", "command", {"code": "await page.locator('#name').fill('Ada')\nawait page.get_by_role('button', name='Save').click()"})
            yield StreamDone("tool_use")
        else:
            assert await page.locator("p").inner_text() == "Saved: Ada"
            yield TextDelta("Saved successfully.")
            yield StreamDone("stop")

    monkeypatch.setattr(BrowserAgent, "system_messages", system)
    monkeypatch.setattr(type(environment_manager), "get_state", lambda self, name, **kw: state(name, **kw))
    monkeypatch.setattr(type(environment_manager), "get", lambda self, name: environment(name))
    monkeypatch.setattr(type(environment_manager), "get_info", lambda self, name: environment(name))
    monkeypatch.setattr(type(model_manager), "stream", lambda self, **kw: stream(**kw))
    kernel = Kernel()
    agent = BrowserAgent(router=Router(), use_memory=False, max_step=4)
    try:
        proc = await kernel.spawn(agent, "Save Ada through the page", ctx=ctx)
        response = await kernel.wait(proc, timeout=15)
        assert response.success, response.message
        assert len(observed) == 3
        assert page.is_closed()  # on_exit released the actual browser context.
    finally:
        await kernel.shutdown(timeout=5)


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
