#!/usr/bin/env bash
# Launch openvscode-server for one session.
#
# OpenSandbox ignores the image ENTRYPOINT and passes its own command, so
# VscodeSandbox hands this script over explicitly as the entrypoint (the same
# reason the chrome-vnc sandbox passes /usr/local/bin/entrypoint-vnc).
#
# Extensions and user data live on MOUNTED directories keyed by OWNER, not by
# session, so installed plugins and settings survive across sessions even though
# the container itself is per-session.
set -euo pipefail

EXT_DIR="${IDE_EXTENSIONS_DIR:-/ide/extensions}"
DATA_DIR="${IDE_USER_DATA_DIR:-/ide/user-data}"
FOLDER="${IDE_FOLDER:-/workspace}"
PORT="${IDE_PORT:-3000}"

mkdir -p "$EXT_DIR" "$DATA_DIR" "$DATA_DIR/server" "$FOLDER"

# --without-connection-token: the port is bound to loopback by the opensandbox
# proxy and only reachable through the gateway-authorised route, so VS Code's
# own token would be a second, redundant secret in every asset URL.
exec "${OPENVSCODE_SERVER_ROOT}/bin/openvscode-server" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --without-connection-token \
    --telemetry-level off \
    --extensions-dir "$EXT_DIR" \
    --user-data-dir "$DATA_DIR" \
    --server-data-dir "$DATA_DIR/server"
