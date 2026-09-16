---
name: environment_default_computer
description: "A full Linux desktop as an ECP environment: mouse and keyboard actions against a containerised XFCE session, screenshots plus an accessibility tree for grounding, and a live noVNC view. `ENVIRONMENT.md` is the machine-readable registration document; `environment.py` defines the actions and `agentevolver/sandbox/default/computer.py` owns the desktop container."
version: 1.0.0
type: module
category: environment
requirements: []
metadata: {}
---
# Computer environment

A whole desktop the agent drives with mouse and keyboard, watched live over noVNC.
`ENVIRONMENT.md` is the machine-readable registration document; `environment.py`
defines the actions, and the desktop container itself is the `computer` sandbox in
`agentevolver/sandbox/default/computer.py`.

## Why a desktop and not a browser

The browser environment reaches a web page through Playwright, which means it also
gets the page's *structure* — a DOM with real elements to click. That is a better
interface whenever the target is a web page, and it should stay the first choice.

This one gives up that structure to gain reach. Anything drawn on the screen is
reachable: an editor, a terminal emulator, a desktop chat client, a file manager,
and a browser among them. No per-application integration exists or is needed,
because the interface is the one every GUI application already has.

Both are the same ECP shape — `@action` methods, `get_state`, `live_view` — so the
agent binding, the frontend view and the capability listing are unchanged. The
difference is entirely in the backend: one headless page versus a whole desktop.

## Grounding: elements before pixels

Every observation is a screenshot. Where the applications support AT-SPI, it is
also a list of elements with roles, names and boxes, drawn onto the screenshot as
numbered marks when set-of-marks is on.

`click_element` takes an id from that list and clicks its centre; `click` takes raw
coordinates. Prefer the first. A coordinate is a claim about where something was
drawn at the moment the screenshot was taken, and it is wrong as soon as a window
moves, a menu opens, or a list scrolls — while a click on the wrong thing still
reports success, because the mouse did move and the button did go down.

Not every application exposes an accessibility tree. When the list comes back
empty, coordinates read off the screenshot are all there is, and the screenshot has
to be re-taken between actions rather than trusted across them.

## The desktop container

Built from `docker/computer/Dockerfile` on the OpenSandbox chrome image, adding
Xvfb, XFCE, x11vnc, websockify/noVNC, `xdotool`, `scrot`, AT-SPI and CJK fonts.

The entrypoint is deliberately *not* overridden: the OpenSandbox agent has to keep
running, because the sandbox's `run_command` channel is how input is injected and
screenshots are taken. The desktop is started on demand afterwards by
`start-desktop`, not by the entrypoint.

The image builds itself on first use, which takes several minutes. That is a slow
first click rather than a failure, but building it ahead of time is kinder:

```
docker build -t agentevolver/computer:latest docker/computer
```

## One desktop, one mouse

Access is serialized per session. A desktop has a single pointer and a single
keyboard focus, so two actions running at once do not interleave harmlessly the way
two independent shell commands would — they fight over the same cursor, and the
result is neither of the two things that were asked for.

## The live view

The container's websockify endpoint is exposed as a `vnc` view, which the gateway
relays on its own origin so the ephemeral port never has to be forwarded — the same
route the browser environment's noVNC view already takes.
