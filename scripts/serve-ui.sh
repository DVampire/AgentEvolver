#!/usr/bin/env bash
set -euo pipefail

# Start the AgentEvolver backend Gateway AND the browser UI together — both inside
# the base sandbox (Model X). This is the command you hand to run-in-sandbox.sh:
#
#   scripts/run-in-sandbox.sh -- scripts/serve-ui.sh
#
# The sandbox runs with --network host, so both servers bind the container's
# loopback, which IS the host loopback. From the host browser:
#   frontend  http://127.0.0.1:5173   (the Vite dev server, serves the SPA)
#   backend   ws://127.0.0.1:9876/ws  (the Gateway the SPA connects to)
#
# Ports are overridable via GATEWAY_PORT / UI_PORT. Extra args are forwarded to
# `agentevolver serve` (e.g. --token, --allow-origin).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATEWAY_PORT="${GATEWAY_PORT:-9876}"
UI_PORT="${UI_PORT:-5173}"

GW_PID=""
cleanup() {
  [[ -n "${GW_PID}" ]] && kill "${GW_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "AgentEvolver — serve UI in sandbox (Model X)"
echo "  backend  : ws://127.0.0.1:${GATEWAY_PORT}/ws"
echo "  frontend : http://127.0.0.1:${UI_PORT}"

# Backend Gateway in the background. It is the framework itself, so it must run
# under Model X (here, inside this container). Extra args ($@) pass straight through.
agentevolver serve --transport websocket --host 127.0.0.1 --port "${GATEWAY_PORT}" "$@" &
GW_PID=$!

# Frontend deps: the repo is bind-mounted, so node_modules is absent on the first
# launch. Install once (persists on the host via the mount); skip if already present.
cd "${REPO_ROOT}/frontend"
if [[ ! -d node_modules ]]; then
  echo "| 📦 Installing frontend deps (first launch only)…"
  npm install
fi

# Vite dev server in the foreground; Ctrl-C stops it and the trap kills the Gateway.
exec npm run dev -- --port "${UI_PORT}"
