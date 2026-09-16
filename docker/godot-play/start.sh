#!/bin/bash
set -euo pipefail
children=()
cleanup() {
    trap - EXIT INT TERM
    for child in "${children[@]}"; do kill "$child" 2>/dev/null || true; done
    wait || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -ac > /tmp/preview-xvfb.log 2>&1 &
children+=("$!")
for attempt in {1..100}; do
    [ -S /tmp/.X11-unix/X99 ] && break
    kill -0 "${children[0]}"
    sleep 0.1
done
timeout --kill-after=3s 120 godot --headless --path "$PWD" --import > /tmp/preview-import.log 2>&1
cat /tmp/preview-import.log
if grep -Eq 'SCRIPT ERROR:|ERROR:|Parse Error:' /tmp/preview-import.log; then exit 1; fi
godot --path "$PWD" --rendering-method gl_compatibility --audio-driver Dummy \
    --resolution 1280x800 --position 0,0 --fullscreen > /tmp/preview-game.log 2>&1 &
game_pid=$!
children+=("$game_pid")
ready=false
for attempt in {1..100}; do
    kill -0 "$game_pid" || { cat /tmp/preview-game.log; exit 1; }
    if xdotool search --onlyvisible --pid "$game_pid" >/dev/null 2>&1; then ready=true; break; fi
    sleep 0.2
done
if [ "$ready" != true ]; then cat /tmp/preview-game.log; exit 1; fi
x11vnc -display :99 -localhost -rfbport 5900 -nopw -forever -shared -noxdamage \
    > /tmp/preview-vnc.log 2>&1 &
children+=("$!")
websockify --web=/opt/godot-play/public "0.0.0.0:${PORT}" 127.0.0.1:5900 &
children+=("$!")
# The browser endpoint ends when any essential process exits. Deploy stop removes
# the owned Docker sandbox, including the game, display and all input connections.
wait -n "${children[@]}"
