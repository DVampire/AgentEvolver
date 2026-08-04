#!/usr/bin/env bash
# Bring up the desktop stack on demand: a virtual display, the XFCE session, and
# the VNC->websockify bridge the live view connects to. Idempotent — safe to call
# more than once. Run via the sandbox's run_command once the container is up
# (NOT as the container entrypoint, so the OpenSandbox agent keeps serving
# run_command for input injection / screenshots).
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-:99}"
# The starting size, and the one a client that cannot resize is stuck with.
SCREEN_GEOMETRY="${SCREEN_GEOMETRY:-1920x1080x24}"
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
# Load the Xft settings before anything draws with them: a toolkit reads X resources
# once at startup, so applying them after the session is up leaves every already-running
# window rendering at the guessed defaults.
[ -f /root/.Xresources ] && xrdb -merge /root/.Xresources 2>/dev/null || true

# A session bus for the desktop apps that expect one. No *system* bus and no logind:
# nothing started here asks for a seat, which is exactly why this shape works in a
# container at all.
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    eval "$(dbus-launch --sh-syntax)" 2>/dev/null || true
fi

# Software rendering: there is no GPU here, and mutter will try for one unless told.
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export XDG_CURRENT_DESKTOP=GNOME
export XDG_SESSION_TYPE=x11
export GTK_THEME=Yaru
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-root}"
mkdir -p "${XDG_RUNTIME_DIR}" && chmod 700 "${XDG_RUNTIME_DIR}"

# Yaru and the Ubuntu font, so the parts that are Ubuntu's look like it.
mkdir -p /root/.config/gtk-3.0
cat > /root/.config/gtk-3.0/settings.ini <<'GTKINI'
[Settings]
gtk-theme-name=Yaru
gtk-icon-theme-name=Yaru
gtk-font-name=Ubuntu 11
gtk-application-prefer-dark-theme=1
GTKINI

# Mutter as a plain window manager on the existing X display — `--x11` keeps it from
# trying to be a display server, which is Xvfb's job here.
mutter --x11 --sm-disable >/tmp/mutter.log 2>&1 &
for _ in $(seq 1 30); do pgrep -x mutter >/dev/null 2>&1 && break; sleep 0.5; done
if ! pgrep -x mutter >/dev/null 2>&1; then
    echo "mutter did not start; see /tmp/mutter.log" >&2
    tail -20 /tmp/mutter.log >&2 2>/dev/null || true
    exit 1
fi

# The panel, so the desktop has somewhere to show what is running.
tint2 >/tmp/tint2.log 2>&1 &

# 3) VNC server on the display + the websockify bridge noVNC connects to.
# -xrandr resize: honour a client asking the desktop to match its window, which is
# what keeps the picture 1:1 instead of upscaled and soft. Xvfb is started with
# +extension RANDR above precisely so this can work.
x11vnc -display "${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_PORT}" \
       -xrandr resize -noxdamage -quiet >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" >/tmp/websockify.log 2>&1 &

echo "desktop started on ${DISPLAY_NUM} (${SCREEN_GEOMETRY}), noVNC on ${NOVNC_PORT}"
