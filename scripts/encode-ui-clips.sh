#!/usr/bin/env bash
# Encode verified recordings and their intentional poster frames for docs/ui.html.
# Usage: scripts/encode-ui-clips.sh <recording-dir> <asset-dir>
set -euo pipefail

SRC="${1:?usage: encode-ui-clips.sh <clips-dir> <out-dir>}"
OUT="${2:?usage: encode-ui-clips.sh <clips-dir> <out-dir>}"
SPEED="${SPEED:-1.10}"
WIDTH="${WIDTH:-1600}"

[ -f "$SRC/manifest.json" ] || { echo 'Recording manifest is required.' >&2; exit 1; }
mkdir -p "$OUT"
STAGING=$(mktemp -d "$OUT/.encoding-XXXXXX")
trap 'rm -rf "$STAGING"' EXIT

# Only complete clips enter this manifest. Failed recordings are never encoded.
python3 - "$SRC/manifest.json" > "$STAGING/clips.tsv" <<'PY'
import json, re, sys
from pathlib import Path
for name, record in sorted(json.loads(Path(sys.argv[1]).read_text()).items()):
    assert re.fullmatch(r'\d{2}-[a-z]+', name), name
    start = float(record['start_seconds'])
    duration = float(record['duration_seconds'])
    assert start >= 0 and 3 < duration < 180, (name, start, duration)
    print(name, start, duration, sep='\t')
PY

while IFS=$'\t' read -r name start duration; do
  ffmpeg -nostdin -v error -y -ss "$start" -i "$SRC/$name.webm" -t "$duration" \
    -vf "setpts=PTS/${SPEED},scale=${WIDTH}:-2:flags=lanczos,format=yuv420p" \
    -c:v libx264 -threads 4 -preset slow -crf 25 -profile:v high \
    -movflags +faststart -an "$STAGING/$name.mp4"
  ffmpeg -nostdin -v error -y -i "$SRC/$name.jpg" \
    -vf "scale=${WIDTH}:-2:flags=lanczos" -frames:v 1 -q:v 3 "$STAGING/$name.jpg"
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$STAGING/$name.mp4" \
    | awk -v name="$name" '{printf "%s: %.1fs\n", name, $1}'
done < "$STAGING/clips.tsv"

for asset in "$STAGING"/*.mp4 "$STAGING"/*.jpg; do
  [ -f "$asset" ] || continue
  mv "$asset" "$OUT/"
done
