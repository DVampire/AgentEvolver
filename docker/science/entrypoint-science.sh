#!/usr/bin/env bash
# Launch JupyterLab for one session's Science workstation.
#
# Serves under a sub-path (--ServerApp.base_url), the same trick the Code view
# uses with openvscode-server's --server-base-path: every absolute URL Jupyter
# emits already carries the prefix, so the UI can host the Lab on ITS OWN origin
# at /science/<session>/ instead of a per-session hostname that only resolves
# when the browser sits on the server.
set -euo pipefail

WORKDIR="${SCIENCE_WORKSPACE:-/workspace}"
PORT="${SCIENCE_PORT:-8888}"
BASE_URL="${SCIENCE_BASE_URL:-/}"

mkdir -p "$WORKDIR"

# The image is built on agentevolver/base, so the agent's conda environment is
# right here — the Lab's terminals and kernels open in the same Python the agent
# uses, and `import agentevolver` works in a notebook.
export PATH="/opt/conda/bin:${PATH}"
# Matplotlib and friends want a writable home; $HOME may be a fresh mount.
export HOME="${HOME:-/root}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${HOME}/.config/matplotlib}"
mkdir -p "$MPLCONFIGDIR"

# --ServerApp.token='': the port is bound to loopback and only reachable through
# the gateway's authorised proxy route, so Jupyter's own token would be a second
# secret in every asset URL for no added protection. Same reasoning as
# openvscode-server's --without-connection-token.
#
# --allow-root: this container runs as root so it can write workspace files
# owned by the host user (see the Dockerfile).
#
# Forwarding traps: signal handling is left to Jupyter itself via exec, so
# `docker stop` reaches it directly rather than a shell that would have to
# relay — the base entrypoint's problem, which this script does not inherit.
exec jupyter lab \
    --ip=0.0.0.0 \
    --port="$PORT" \
    --no-browser \
    --allow-root \
    --notebook-dir="$WORKDIR" \
    --ServerApp.base_url="$BASE_URL" \
    --ServerApp.token='' \
    --ServerApp.password='' \
    --ServerApp.disable_check_xsrf=True \
    --ServerApp.allow_origin='*' \
    --ServerApp.allow_remote_access=True \
    --ServerApp.trust_xheaders=True
