#!/usr/bin/env bash
# install-bundle.sh — set up the optional provider dependencies for migrated
# Langflow *bundle* plugins.
#
# Each bundle plugin declares its third-party pip packages in the
# `requirements:` field of its PLUGIN.md frontmatter
#   agentevolver/plugins/default/<bundle>/PLUGIN.md
# Provider libraries are imported lazily, so a bundle registers and shows up on
# the canvas even without them; install them here to actually run the bundle.
#
# Usage:
#   scripts/install-bundle.sh <bundle> [<bundle> ...]   # install those bundles' deps
#   scripts/install-bundle.sh --all                     # install every bundle's deps
#   scripts/install-bundle.sh --list [<bundle>]         # print deps, install nothing
#   scripts/install-bundle.sh --names                   # list all bundle names
#
# Honors $PIP (default: "pip") so you can use "uv pip", "pip3", etc.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_DIR="$ROOT/agentevolver/plugins/default"
PIP="${PIP:-pip}"

# Read the `requirements: [a, b, c]` list from a bundle's PLUGIN.md → space-separated.
bundle_reqs() {
  local md="$DEFAULT_DIR/$1/PLUGIN.md"
  [ -f "$md" ] || return 0
  grep -m1 -E '^requirements:' "$md" 2>/dev/null \
    | sed -E 's/^requirements:\s*\[//; s/\]\s*$//; s/,/ /g' \
    | tr -s ' '
}

all_bundles() {
  find "$DEFAULT_DIR" -mindepth 1 -maxdepth 1 -type d -exec test -e '{}/PLUGIN.md' ';' -print \
    | xargs -n1 basename | sort
}

[ $# -ge 1 ] || { grep -E '^#( |$)' "$0" | sed -E 's/^# ?//'; exit 1; }

case "$1" in
  --names)
    all_bundles; exit 0 ;;
  --list)
    shift
    targets=("$@"); [ ${#targets[@]} -gt 0 ] || mapfile -t targets < <(all_bundles)
    for b in "${targets[@]}"; do printf '%-18s %s\n' "$b" "$(bundle_reqs "$b")"; done
    exit 0 ;;
  --all)
    mapfile -t targets < <(all_bundles) ;;
  *)
    targets=("$@") ;;
esac

# Collect the union of requirements across the selected bundles.
declare -A seen; pkgs=()
for b in "${targets[@]}"; do
  [ -d "$DEFAULT_DIR/$b" ] || { echo "⚠️  unknown bundle: $b (see --names)"; continue; }
  for p in $(bundle_reqs "$b"); do
    [ -n "${seen[$p]:-}" ] || { seen[$p]=1; pkgs+=("$p"); }
  done
done

if [ ${#pkgs[@]} -eq 0 ]; then
  echo "No third-party requirements for: ${targets[*]} (nothing to install)."
  exit 0
fi

echo "Installing ${#pkgs[@]} package(s) for bundle(s) '${targets[*]}':"
printf '  %s\n' "${pkgs[@]}"
exec $PIP install "${pkgs[@]}"
