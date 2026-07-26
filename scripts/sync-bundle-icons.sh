#!/usr/bin/env bash
# Sync migrated-bundle glyphs into the frontend so the palette can render them.
#
# Each bundle plugin keeps its preserved Langflow SVG as the source of truth at
#   agentevolver/plugins/default/<bundle>/resources/icon.svg
# The frontend resolves NodeSpec.icon "bundle:<bundle>" to
#   frontend/src/icons/bundles/<bundle>.svg
# Re-run this after adding or updating any bundle icon.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/agentevolver/plugins/default"
DST="$ROOT/frontend/src/icons/bundles"
mkdir -p "$DST"
n=0
for f in "$SRC"/*/resources/icon.svg; do
  [ -e "$f" ] || continue
  b="$(basename "$(dirname "$(dirname "$f")")")"
  cp "$f" "$DST/$b.svg"
  n=$((n + 1))
done
echo "Synced $n bundle icons -> $DST"
