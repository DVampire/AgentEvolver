"""Browser checks for independent series, honest stacks and shared widget state."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from agentevolver.visual.usage.server import UsageView

playwright = pytest.importorskip("playwright.sync_api")
ASSETS = Path(__file__).resolve().parents[1] / "agentevolver" / "visual" / "usage"


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            instance = p.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        yield instance
        instance.close()


@pytest.fixture
def usage_page(browser, tmp_path):
    trace = tmp_path / "trace"
    trace.mkdir()
    rows = []
    for i, (uncached, read, write, output) in enumerate(
        [(10, 80, 10, 20), (20, 40, 0, 30), (5, 0, 5, 10)]
    ):
        rows.append(
            {
                "id": str(i),
                "event_type": "agent_call",
                "session_id": "s",
                "agent_name": "Builder",
                "task_id": "builder",
                "step_number": i,
                "timestamp": [
                    "2026-09-08T00:00:00Z",
                    "2026-09-08T00:00:20Z",
                    "2026-09-08T00:01:00Z",
                ][i],
                "usage": {
                    "input_tokens": uncached,
                    "cache_read_tokens": read,
                    "cache_write_tokens": write,
                    "context_input_tokens": uncached + read + write,
                    "output_tokens": output,
                    "reasoning_tokens": 5,
                    "cost": 0.1,
                },
            }
        )
    (trace / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    view = UsageView(lambda: [{"id": "attempt", "log_root": str(tmp_path)}])
    page = browser.new_page(viewport={"width": 1440, "height": 1200})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )

    def serve(route):
        path = urlsplit(route.request.url)
        if path.path == "/api/usage":
            body, mime = view.response(path.query)
        elif path.path in {"/usage.js", "/usage.css"}:
            name, mime = (
                ("app.js", "text/javascript")
                if path.path.endswith("js")
                else ("style.css", "text/css")
            )
            body = (ASSETS / name).read_bytes()
        elif path.path == "/boot.js":
            mime = "text/javascript"
            body = b"""
              import {mountUsage} from '/usage.js';
              window.one = mountUsage(document.querySelector('#one'), {initialFilters:{metric:'tokens'}});
              window.two = mountUsage(document.querySelector('#two'), {initialFilters:{metric:'tokens'}, initialTokenSeries:['output_tokens']});
            """
        else:
            mime = "text/html"
            body = b"""<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="/usage.css"><main id="one"></main><main id="two"></main>
            <script type="module" src="/boot.js"></script>"""
        route.fulfill(
            body=body,
            content_type=mime,
            headers={
                "Content-Security-Policy": "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            },
        )

    page.route("http://usage.test/**", serve)
    page.goto("http://usage.test/")
    page.locator("#one[data-loaded=true]").wait_for()
    page.locator("#two[data-loaded=true]").wait_for()
    yield page
    page.close()
    assert not errors


def series(page, root="#one", style="line"):
    return set(
        page.locator(f"{root} [data-series-style={style}]").evaluate_all(
            "nodes => nodes.map(n => n.dataset.drawnSeries)"
        )
    )


def test_selection_rescales_and_survives_refresh_without_changing_totals(usage_page):
    page = usage_page
    main = page.locator("#one")
    assert series(page) == {"input_tokens", "output_tokens", "cache_tokens", "total_tokens"}
    assert series(page, "#two") == {"output_tokens"}
    assert main.locator(".u-token-primary i").evaluate_all(
        "es=>es.map(e=>getComputedStyle(e).backgroundColor)"
    ) == ["rgb(91, 225, 177)", "rgb(243, 190, 109)", "rgb(117, 173, 255)", "rgb(236, 248, 242)"]
    total = main.locator(".u-cards").inner_text()
    old_max = float(main.locator(".u-chart").get_attribute("data-y-max"))
    main.locator("[data-token-series=total_tokens]").uncheck()
    assert "total_tokens" not in series(page)
    assert float(main.locator(".u-chart").get_attribute("data-y-max")) < old_max
    assert main.locator(".u-cards").inner_text() == total
    page.evaluate("async () => { await window.one.refresh(); }")
    assert "total_tokens" not in series(page)
    assert series(page, "#two") == {"output_tokens"}
    with page.expect_response("**/api/usage?*metric=cost*"):
        main.locator("[data-metric=cost]").click()
    with page.expect_response("**/api/usage?*metric=tokens*"):
        main.locator("[data-metric=tokens]").click()
    playwright.expect(main.locator(".u-token-controls")).to_be_visible()
    assert "total_tokens" not in series(page)
    main.locator("[data-token-preset=none]").click()
    playwright.expect(main.locator(".u-empty")).to_contain_text("No token series selected")
    assert main.locator(".u-cards").inner_text() == total
    page.evaluate("async () => { await window.one.refresh(); }")
    assert not series(page)
    main.locator("[data-token-preset=all]").click()
    assert len(series(page)) == 9


def test_bars_never_stack_total_or_double_count_cache_children(usage_page):
    page = usage_page
    main = page.locator("#one")
    main.locator("[data-kind=bar]").click()
    assert series(page, style="grouped") == {
        "input_tokens",
        "output_tokens",
        "cache_tokens",
        "total_tokens",
    }
    main.locator(".u-token-bar-select").select_option("stacked")
    assert series(page, style="stacked") == {"input_tokens", "output_tokens", "cache_tokens"}
    assert series(page) == {"total_tokens"}
    values = main.locator("[data-stack-total]").evaluate_all(
        "ns=>ns.map(n=>Number(n.dataset.stackTotal))"
    )
    assert values == [120, 90, 20]
    main.locator("[data-token-preset=all]").click()
    assert series(page) == {
        "total_tokens",
        "context_input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    }
    assert (
        main.locator("[data-stack-total]").evaluate_all(
            "ns=>ns.map(n=>Number(n.dataset.stackTotal))"
        )
        == values
    )
    main.locator("[data-token-preset=cache]").click()
    assert series(page, style="stacked") == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    }
    assert (
        main.locator("[data-stack-total]").evaluate_all(
            "ns=>ns.map(n=>Number(n.dataset.stackTotal))"
        )
        == values
    )


def test_cumulative_tooltip_time_buckets_and_mobile_layout(usage_page):
    page = usage_page
    main = page.locator("#one")
    with page.expect_response("**/api/usage?*cumulative=true*"):
        main.locator(".u-cumulative").check()
    playwright.expect(main.locator(".u-chart-title")).to_contain_text("cumulative")
    hit = main.locator("[data-point-index='2']")
    hit.focus()
    tip = main.locator(".u-tooltip")
    playwright.expect(tip).to_contain_text("230")
    for label, number in [("Input (uncached)", "35"), ("Output", "60"), ("Cache", "135")]:
        playwright.expect(
            tip.locator(".u-tip-row").filter(has=page.get_by_text(label, exact=True))
        ).to_contain_text(number)
    with page.expect_response("**/api/usage?*axis=time*"):
        main.locator("[data-axis=time]").click()
    playwright.expect(main.locator("[data-point-index]")).to_have_count(2)
    main.locator("[data-point-index='1']").focus()
    playwright.expect(tip).to_contain_text("230")
    page.set_viewport_size({"width": 390, "height": 844})
    main.locator("[data-token-preset=all]").click()
    main.locator(".u-token-more").evaluate("n=>n.open=true")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
