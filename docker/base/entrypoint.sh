#!/usr/bin/env bash
# Container entrypoint for the AgentEvolver project sandbox (Model X).
#
# Runs the given command as root (so the agent can `pip install` into the conda env
# and reach the Docker socket to spawn peer containers), then fixes ownership of the
# run's outputs: files created by root inside the container would otherwise be
# root-owned on the host through the bind mount. On exit we chown the output tree
# back to whoever owns the mounted project root (the host user), so artifacts stay
# inspectable and deletable on the host.
set -o pipefail

"$@"
status=$?

proj="/AgentEvolver"
owner="$(stat -c '%u:%g' "${proj}" 2>/dev/null || true)"
if [ -n "${owner}" ] && [ "${owner}" != "0:0" ]; then
    # Outputs created by root inside the container would otherwise be root-owned on
    # the host through the bind mount. Also covers frontend/node_modules, which
    # `scripts/serve-ui.sh` installs as root on first launch.
    for d in "${proj}/output" "${proj}/frontend/node_modules"; do
        [ -d "${d}" ] && chown -R "${owner}" "${d}" 2>/dev/null || true
    done
fi

exit "${status}"
