---
name: browser_environment
description: Playwright browser environment for web automation.
version: 1.0.0
type: worker
---

<environment_browser>

## State
The state of the browser environment: the current URL, page title, open tabs, and a list of interactive elements. When set-of-marks (SoM) is on, the state screenshot draws numbered boxes over interactive elements that match the elements list.

## Vision
This environment has vision: a successful observation returns a screenshot of the current page (optionally annotated with numbered element boxes and the last action's cursor/scroll/drag overlay). Use the coordinates from the elements list / boxes to target actions. If capture fails, available DOM/URL evidence and observation errors remain; a missing image does not mean an empty page. A pending JavaScript dialog blocks capture and navigation until explicitly handled.

## Actions

Only actions in the current tool schema are available. Search is omitted when its
external service credentials are missing; use `goto` for known URLs instead.

### click
Click at specified coordinates on the page.
- x (int), y (int): pixel coordinates.
- button (str, optional): "left" (default), "right", or "middle".

### double_click
Double-click at specified coordinates.
- x (int), y (int): pixel coordinates.

### scroll
Scroll at specified coordinates with given offsets.
- x (int), y (int): where to scroll.
- scroll_x (int), scroll_y (int): horizontal/vertical scroll offsets.

### type
Type text at the current cursor position.
- text (str): the text to type.

### wait
Wait for a period before the next observation.
- ms (int, optional): milliseconds to wait (default 1000).

### move
Move the mouse to specified coordinates.
- x (int), y (int): pixel coordinates.

### keypress
Press specified keys.
- keys (list[str]): modifier lists form a chord, e.g. ["Control", "a"]. Without a
  modifier, entries are successive presses, e.g. ["Tab", "Tab", "Enter"]. Common
  aliases such as ESC, CTRL and SPACE are accepted.

### drag
Drag the mouse along a path.
- path (list[[int, int]]): ordered list of [x, y] points.

### goto
Navigate the browser to a URL. Accepts a full URL (https://...) or a bare domain. Use this to open a link found via `search`.
- url (str): the destination URL.

### search
Search the web and get a ranked list of results (title, URL, description) via the Firecrawl API. Runs server-side (not blocked by local IP/CAPTCHA), so prefer it over navigating to a search engine. Discover pages here, then open one with `goto`.
- query (str): the search query.
- num_results (int, optional): number of results (default 5).

### command
Run a Playwright Python snippet with `page` (current Page) and `context` (BrowserContext) in scope. Use as a fallback when coordinate-based actions fail (element not clickable/hidden/moving) or to read structured data. The code runs inside an async function: use `await` directly and `return` to send a value back. Timeout: 30s.
- code (str): the Playwright snippet, e.g. `await page.locator("text=Login").click()` or `return await page.locator(".price").all_inner_texts()`.
Do not install dialog callbacks or automatically accept confirmations; use `handle_dialog` instead.
The outer snippet is Python; JavaScript strings passed to `page.evaluate()` or
`locator.evaluate_all()` are valid. Locator operations default to 5 seconds and
navigation to 10 seconds, within the 30-second command budget. After a missing or
hidden locator, inspect the current DOM before retrying; do not repeatedly wait for
a selector copied from an earlier page or another agent's report.

### handle_dialog
Resolve the pending alert, confirmation, or prompt described in environment state.
- accept (bool, required): explicitly accept (`true`) or dismiss (`false`) according to the task and dialog message.
- prompt_text (str, optional): text to submit when accepting a prompt.
Accepting can complete the original irreversible action. Do not repeat the action that opened the dialog. Merely detecting a dialog does not accept it.

## Interaction
Input format: a JSON string with action-specific parameters.
Example: {"name": "goto", "args": {"url": "https://example.com"}}
Example: {"name": "click", "args": {"x": 480, "y": 320, "button": "left"}}

</environment_browser>
