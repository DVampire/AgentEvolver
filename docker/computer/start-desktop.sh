#!/usr/bin/env bash
# Bring up the desktop stack on demand: a virtual display, the XFCE session, and
# the VNC->websockify bridge the live view connects to. Idempotent — safe to call
# more than once. Run via the sandbox's run_command once the container is up
# (NOT as the container entrypoint, so the OpenSandbox agent keeps serving
# run_command for input injection / screenshots).
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-:99}"
SCREEN_GEOMETRY="${SCREEN_GEOMETRY:-1280x800x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
export DISPLAY="${DISPLAY_NUM}"

# Already running on this display? Nothing to do.
if xdpyinfo -display "${DISPLAY_NUM}" >/dev/null 2>&1; then
    echo "desktop already running on ${DISPLAY_NUM}"
    exit 0
fi

# 1) Virtual display.
Xvfb "${DISPLAY_NUM}" -screen 0 "${SCREEN_GEOMETRY}" -ac +extension RANDR +extension GLX >/tmp/xvfb.log 2>&1 &
for _ in $(seq 1 60); do xdpyinfo -display "${DISPLAY_NUM}" >/dev/null 2>&1 && break; sleep 0.2; done

# 2) XFCE session on its own dbus (dbus is also what AT-SPI accessibility rides).
export NO_AT_BRIDGE=0
dbus-launch --exit-with-session startxfce4 >/tmp/xfce.log 2>&1 &

# 3) VNC server on the display + the websockify bridge noVNC connects to.
x11vnc -display "${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_PORT}" -quiet >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" >/tmp/websockify.log 2>&1 &

echo "desktop started on ${DISPLAY_NUM} (${SCREEN_GEOMETRY}), noVNC on ${NOVNC_PORT}"
