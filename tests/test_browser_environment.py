"""Simple integration test for BrowserEnvironment."""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.environment.default.browser.environment import BrowserEnvironment


async def main():
    env = BrowserEnvironment(
        base_dir="tests/browser_output",
        headless=True,
    )

    await env.initialize()

    # 1. get_state — check URL and screenshot are present
    print("--- get_state ---")
    state = await env.get_state()
    print(f"URL   : {state['extra']['url']}")
    print(f"Title : {state['extra']['title']}")
    print(f"Tabs  : {state['extra']['tabs']}")
    assert state["extra"]["url"], "Expected a non-empty URL"
    assert state["extra"]["screenshots"], "Expected screenshots in state"

    # 2. click — middle of the page
    print("\n--- click ---")
    result = await env.click(x=512, y=384)
    print(f"success: {result['success']}  message: {result['message']}")
    assert result["success"]

    # 3. type — search something
    print("\n--- type ---")
    result = await env.type_text(text="playwright browser test")
    print(f"success: {result['success']}  message: {result['message']}")
    assert result["success"]

    # 4. keypress — press Enter
    print("\n--- keypress ---")
    result = await env.keypress(keys=["Enter"])
    print(f"success: {result['success']}  message: {result['message']}")
    assert result["success"]

    # 5. wait — let the page settle
    print("\n--- wait ---")
    result = await env.wait(ms=1500)
    print(f"success: {result['success']}  message: {result['message']}")
    assert result["success"]

    # 6. get_state again — URL should have changed after search
    print("\n--- get_state (after search) ---")
    state = await env.get_state()
    print(f"URL   : {state['extra']['url']}")
    print(f"Title : {state['extra']['title']}")

    # 7. scroll
    print("\n--- scroll ---")
    result = await env.scroll(x=512, y=400, scroll_x=0, scroll_y=300)
    print(f"success: {result['success']}  message: {result['message']}")
    assert result["success"]

    # 8. move
    print("\n--- move ---")
    result = await env.move(x=200, y=200)
    print(f"success: {result['success']}  message: {result['message']}")
    assert result["success"]

    await env.cleanup()
    print("\n✅ All checks passed")


if __name__ == "__main__":
    asyncio.run(main())
