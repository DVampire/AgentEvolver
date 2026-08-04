#!/usr/bin/env bash
# Bring up the desktop stack on demand: a virtual display, a window manager, the
# desktop surface and panel, and the VNC->websockify bridge the live view connects to.
# Idempotent — safe to call more than once. Run via the sandbox's run_command once the
# container is up, not as the entrypoint: the container's PID 1 has to stay alive for
# run_command to keep working at all.
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
# Both, and in this order: the Applications menu hides entries marked NotShowIn for the
# current desktop, so naming only one of the two halves this desktop is built from drops
# real applications out of the only menu there is.
export XDG_CURRENT_DESKTOP=XFCE:GNOME
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

# The desktop itself. Mutter manages windows and nothing else — without these three
# there is no menu, no icons and no way to launch anything, which looks exactly like a
# desktop that failed to start.
#
# xfsettingsd first: it owns XSETTINGS, so it is what actually applies the GTK theme,
# the icon theme and the font DPI to everything that starts afterwards.
xfsettingsd --replace >/tmp/xfsettingsd.log 2>&1 &
for _ in $(seq 1 20); do pgrep -x xfsettingsd >/dev/null 2>&1 && break; sleep 0.25; done

# Theme and DPI through xfconf, which is where xfsettingsd reads them from.
#
# DPI is stored here in real dots per inch. The XSETTINGS *protocol* carries it in
# 1/1024ths and xfsettingsd does that multiplication itself — writing the protocol
# number into this key means asking for 96 and getting 98304, which renders as a
# desktop of enormous fuzzy letters.
xfconf-query -c xsettings -p /Net/ThemeName        -n -t string -s Yaru      2>/dev/null || true
xfconf-query -c xsettings -p /Net/IconThemeName    -n -t string -s Yaru      2>/dev/null || true
xfconf-query -c xsettings -p /Gtk/FontName         -n -t string -s "Ubuntu 11" 2>/dev/null || true
xfconf-query -c xsettings -p /Xft/DPI              -n -t int    -s 96        2>/dev/null || true
xfconf-query -c xsettings -p /Xft/Antialias        -n -t int    -s 1         2>/dev/null || true
xfconf-query -c xsettings -p /Xft/HintStyle        -n -t string -s hintslight 2>/dev/null || true
xfconf-query -c xsettings -p /Xft/RGBA             -n -t string -s rgb       2>/dev/null || true

# The wallpaper. Which monitor key applies depends on the output's name, and under Xvfb
# that is whatever RANDR decided to call it — so ask, rather than guess and silently
# write a key nothing reads.
MONITOR="$(xrandr --query 2>/dev/null | awk '/ connected/{print $1; exit}')"
WALLPAPER="$(ls /usr/share/backgrounds/*.png /usr/share/backgrounds/*.jpg 2>/dev/null | head -1)"
if [ -n "${MONITOR}" ] && [ -n "${WALLPAPER}" ]; then
    BACKDROP="/backdrop/screen0/monitor${MONITOR}/workspace0"
    xfconf-query -c xfce4-desktop -p "${BACKDROP}/last-image" -n -t string -s "${WALLPAPER}" 2>/dev/null || true
    xfconf-query -c xfce4-desktop -p "${BACKDROP}/image-style" -n -t int -s 5 2>/dev/null || true
fi

# The desktop surface (wallpaper, icons, right-click menu) and the panel (Applications
# menu, window list, tray, clock).
xfdesktop >/tmp/xfdesktop.log 2>&1 &
xfce4-panel >/tmp/xfce4-panel.log 2>&1 &

# 3) VNC server on the display + the websockify bridge noVNC connects to.
# -xrandr resize: honour a client asking the desktop to match its window, which is
# what keeps the picture 1:1 instead of upscaled and soft. Xvfb is started with
# +extension RANDR above precisely so this can work.
x11vnc -display "${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_PORT}" \
       -xrandr resize -noxdamage -quiet >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" >/tmp/websockify.log 2>&1 &

echo "desktop started on ${DISPLAY_NUM} (${SCREEN_GEOMETRY}), noVNC on ${NOVNC_PORT}"
