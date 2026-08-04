---
name: computer_environment
description: A full Linux desktop operated with mouse and keyboard — any GUI application, watchable live over noVNC.
version: 1.0.0
type: worker
---

<environment_computer>

## What this is

A whole desktop, not a browser. The browser environment can only reach a web page; this
one can reach anything running on the screen — an editor, a terminal window, a chat
client, a file manager, and a browser among them. The cost is that nothing here knows
what an "element" is the way a DOM does: you are moving a mouse and pressing keys.

The desktop lives in a container, one per session. It starts on first use and is watched
live over noVNC, so a human can see exactly what the agent is doing while it happens.

## State

Every observation returns a screenshot. When the accessibility tree is available, it also
returns a list of on-screen elements, each with an `id`, a role, a name, and its bounding
box; with set-of-marks on, those are drawn onto the screenshot as numbered boxes that
match the ids.

Prefer `click_element` over `click` whenever the element you want is in that list. Pixel
coordinates are a guess about where something is drawn and go stale the moment a window
moves; an element id is the thing itself. Applications without AT-SPI support return no
elements at all, and then coordinates are all there is — read them off the screenshot.

## Vision

Yes. Every state carries a PNG of the current screen. Look at it before acting and look
again afterwards: this environment has no other way to tell you whether a click landed,
and a click on the wrong thing usually still "succeeds".

## Actions

### screenshot
Capture the desktop now. `get_state` already includes one, so use this when you want a
fresh look between actions rather than at the start of a step.

### click
- x (int), y (int): where, in screen pixels.
- button (str, optional): `left` (default), `middle`, `right`.

### click_element
- element_id (int): an `id` from the element list.

Clicks the centre of that element. This is the reliable one — see State.

### double_click
- x (int), y (int).

### move
- x (int), y (int): move the pointer without clicking. Useful to trigger a hover state
  before looking again.

### drag
- x1 (int), y1 (int), x2 (int), y2 (int): press at the first point, release at the second.

### scroll
- x (int), y (int): where to scroll.
- amount (int, optional): positive scrolls down, negative up. Default 3.

### type
- text (str): typed at whatever currently has focus.

Focus is not implied. Click the field first, take a screenshot, and confirm the caret is
where you think it is — text typed into the wrong window is silently accepted.

### keypress
- keys (str): a key or a combination, e.g. `Return`, `ctrl+c`, `alt+Tab`, `super`.

### open_app
- command (str): launch a program, e.g. `chromium --no-sandbox https://example.com`.

Launching is not the same as being ready. A program takes a moment to draw its window;
`wait` and then look before you act on it.

### wait
- ms (int, optional): milliseconds, default 1000.

The only way to let the screen catch up. GUI applications animate, load and repaint, and
acting on a half-drawn window is how a correct sequence of actions produces a wrong
result.

</environment_computer>
