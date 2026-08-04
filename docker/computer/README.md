# computer — a Linux desktop the agent drives with mouse/keyboard

A full X11 desktop (XFCE) in a container. The agent operates it with generic
mouse/keyboard actions and watches it live over noVNC — a general "computer-use"
environment, so any GUI app (a browser for Bilibili, Telegram, …) works without
per-app integration. It generalizes `docker/chrome-vnc/` (which is browser-only).

## What it adds on top of `opensandbox/chrome`
- **Xvfb** — a virtual X11 display (`:99`).
- **XFCE** — a real desktop (panel, launcher, file manager, terminal).
- **x11vnc + websockify + noVNC** — the live view on `:6080`.
- **xdotool** — mouse/keyboard injection (the control channel).
- **scrot** — screen capture (`get_state` screenshots).
- **AT-SPI** (`at-spi2-core`, `python3-pyatspi`) — the accessibility tree used for
  grounding (Set-of-Marks element boxes), so the agent clicks elements by id, not
  raw pixels.
- **CJK fonts** — so apps like Bilibili/WeChat render text.

## Why the entrypoint is NOT overridden
The image keeps the OpenSandbox agent as its entrypoint so the sandbox's
`run_command` channel stays alive — that is how the environment injects input and
takes screenshots. The desktop is started on demand by `/usr/local/bin/start-desktop`
(called via `run_command` once the container is up), never by the entrypoint.

## Ports
| Port | Purpose |
| --- | --- |
| 5900 | raw VNC (internal) |
| 6080 | websockify (RFB over WebSocket) — the noVNC live-view endpoint |

Both are mapped to ephemeral host ports by the opensandbox proxy and registered
in the central port registry (kind=`env`).

## Build
Auto-built on first use by `sandbox/default/computer.py` (`DesktopSandbox`), or:

```bash
docker build -t agentevolver/computer:latest docker/computer/
```

## Resolution
Configurable per environment: `start-desktop` reads `SCREEN_GEOMETRY`
(e.g. `1920x1080x24`), and `DesktopSandbox.start_desktop(width=, height=)` sets it.

## Provider abstraction
`DesktopSandbox` is the `docker-linux` provider — the default: fits opensandbox,
spawns in seconds, pools. Windows/macOS would be separate heavyweight VM providers
(Apple hardware / licensing / no pooling) exposing the same start_desktop /
vnc_ws_url / run surface; the environment on top does not change.
