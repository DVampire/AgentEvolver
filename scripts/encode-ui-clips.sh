#!/usr/bin/env bash
# Turn the webms from record-ui-clips.py into what docs/ui.html embeds:
# h264 mp4 (playable everywhere, small) plus a poster frame per clip.
#
#   scripts/encode-ui-clips.sh /tmp/uiclips docs/assets/ui
#
# SPEED and WIDTH are overridable; needs ffmpeg on PATH.
set -euo pipefail

SRC="${1:?usage: encode-ui-clips.sh <clips-dir> <out-dir>}"
OUT="${2:?usage: encode-ui-clips.sh <clips-dir> <out-dir>}"
SPEED="${SPEED:-1.35}"      # tighten the pacing; these are tours, not tutorials
WIDTH="${WIDTH:-1280}"

# Seconds to drop off the front, per clip. The IDE and the kernel each boot a
# container on first use, and a spinner is not worth showing at full length.
trim_for() {
  case "$1" in
    04-code)    echo 9  ;;
    05-science) echo 6  ;;
    *)          echo 0  ;;
  esac
}

# Where the clip is most itself -- used for the poster, so the page does not
# show eight near-identical first frames.
poster_at() {
  case "$1" in
    01-overview) echo 0.55 ;;
    02-views)    echo 0.45 ;;
    *)           echo 0.80 ;;
  esac
}

mkdir -p "$OUT"
total=0

for f in "$SRC"/*.webm; do
  [ -e "$f" ] || continue
  name="$(basename "${f%.webm}")"
  mp4="$OUT/$name.mp4"
  jpg="$OUT/$name.jpg"
  ss="$(trim_for "$name")"

  # -ss before -i seeks cheaply; setpts then speeds up what is left.
  ffmpeg -v error -y -ss "$ss" -i "$f" \
    -vf "setpts=PTS/${SPEED},scale=${WIDTH}:-2:flags=lanczos,format=yuv420p" \
    -c:v libx264 -preset veryslow -crf 30 -profile:v high -level 4.0 \
    -movflags +faststart -an "$mp4"

  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$mp4")
  at=$(python3 -c "print(round(float('$dur')*$(poster_at "$name"), 2))")
  ffmpeg -v error -y -ss "$at" -i "$mp4" -frames:v 1 -q:v 6 "$jpg"

  kb=$(( $(stat -c%s "$mp4") / 1024 ))
  total=$(( total + kb ))
  printf '  %-20s %5s KB  %5.1fs  (trim %ss)\n' "$name.mp4" "$kb" "$dur" "$ss"
done

printf '  %-20s %5s KB total\n' '' "$total"
