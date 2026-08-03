#!/usr/bin/env bash
# Container entry: normalise what the host handed us, then run the real command.
#
# Kept as a file rather than an inline `bash -lc` in run-in-sandbox.sh because that
# meant three levels of nested quoting around a command that itself contains quotes —
# the first attempt died with "unexpected EOF while looking for matching `"'".
set -euo pipefail

# ~/.ssh arrives read-only at /mnt/host-ssh and is copied rather than used in place.
# ssh refuses a config or private key that is group-writable or owned by another user,
# and a bind mount preserves the host's ownership and mode: a perfectly ordinary
# `-rw-rw-r-- 1014 config` becomes "Bad owner or permissions on /root/.ssh/config" and
# every Host alias silently stops working. A copy can be given the modes ssh insists on;
# a mount cannot.
if [[ -d /mnt/host-ssh ]]; then
  mkdir -p /root/.ssh
  cp -r /mnt/host-ssh/. /root/.ssh/ 2>/dev/null || true
  chmod 700 /root/.ssh
  chmod 600 /root/.ssh/* 2>/dev/null || true
  # `known_hosts` is the one file worth writing back to, and it cannot be: the copy dies
  # with the container. Host keys therefore have to be already known on the host, which
  # is the correct default — accepting a new one inside a --rm container would teach
  # nobody anything and would hide a changed key.
fi

exec "$@"
