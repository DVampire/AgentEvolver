#!/bin/bash
set -euo pipefail
export XDG_RUNTIME_DIR=/tmp/godot-xdg
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
chmod 700 "$XDG_RUNTIME_DIR"
mkdir -p "$XDG_DATA_HOME/godot"
if [ ! -e "$XDG_DATA_HOME/godot/export_templates" ]; then
    ln -s /opt/godot/export_templates "$XDG_DATA_HOME/godot/export_templates"
fi
# Rendering stays inside this container. No host display or network port is exposed.
# Ubuntu's xvfb-run redirects the child's stderr into stdout, corrupting MCP.
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -ac >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!
for attempt in {1..50}; do
    kill -0 "$xvfb_pid" || { cat /tmp/xvfb.log >&2; exit 1; }
    if [ -S /tmp/.X11-unix/X99 ]; then
        exec node /opt/godot-mcp/build/index.js
    fi
    sleep 0.1
done
echo 'Xvfb did not become ready' >&2
exit 1
