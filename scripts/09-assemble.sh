#!/usr/bin/env bash
# Stage 9 — Assemble.
# Joins the chapters into the finished video. The intermediates all share one
# format (H.264 CRF 12, PCM sound), so they join without re-encoding and the
# one lossy encode happens here, once.
#
#   → work/<project>/final.mp4          with graphics
#   → work/<project>/clean.mov          the same cut without graphics — the
#                                       thumbnail frame and LinkedIn clip come from it
#
# Usage:  ./scripts/09-assemble.sh [project] [--force]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROJECT=""
for a in "$@"; do case "$a" in -*) ;; *) PROJECT="$a" ;; esac; done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi
DIR="$WORK/$PROJECT"

stage_begin "9 — assemble"
log "Project: $PROJECT"
stage_done "$PROJECT" "08-graphics" || die "Not every chapter has its graphics yet — run ./scripts/08-graphics.sh."
if stage_done "$PROJECT" "09-assemble" && (( ! FORCE )) \
   && [[ -z "$(find "$DIR/graphics" -name composite.mov -newer "$DIR/final.mp4")" ]]; then
  log_ok "Already done — work/$PROJECT/final.mp4"
  stage_end; exit 0
fi

NCH=$("$PY" -c "import json;print(len(json.load(open('$DIR/chapters.json'))['chapters']))")
WANT=$("$PY" -c "import json;print(json.load(open('$DIR/chapters.json'))['duration'])")
: > "$DIR/.final.txt"; : > "$DIR/.clean.txt"
for (( n=1; n<=NCH; n++ )); do
  NN=$(printf '%02d' "$n")
  [[ -f "$DIR/graphics/$NN/composite.mov" ]] || die "Chapter $n has no composite.mov."
  printf "file '%s'\n" "$DIR/graphics/$NN/composite.mov" >> "$DIR/.final.txt"
  printf "file '%s'\n" "$DIR/chapters/$NN.mov" >> "$DIR/.clean.txt"
done

log "Joining $NCH chapters and encoding the finished video"
T0=$(date +%s)
ffmpeg -hide_banner -nostats -v error -y -f concat -safe 0 -i "$DIR/.final.txt" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 \
  -movflags +faststart "$DIR/final.mp4" || die "Couldn't encode final.mp4."
ffmpeg -hide_banner -nostats -v error -y -f concat -safe 0 -i "$DIR/.clean.txt" -c copy "$DIR/clean.mov" \
  || die "Couldn't join the graphics-free copy."
rm -f "$DIR/.final.txt" "$DIR/.clean.txt"

GOT=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$DIR/final.mp4")
awk -v a="$GOT" -v b="$WANT" 'BEGIN{d=a-b; if (d<0) d=-d; exit !(d<0.1)}' \
  || die "final.mp4 is $(hms "$GOT") but the chapters add up to $(hms "$WANT") — something was dropped."
LOUD=$(ffmpeg -hide_banner -nostats -i "$DIR/final.mp4" -vn -af ebur128 -f null - 2>&1 \
  | awk '/Integrated loudness:/{f=1} f&&/ I:/{print $2; exit}')
log_ok "final.mp4 — $(hms "$GOT"), ${LOUD} LUFS, $(( $(stat -f%z "$DIR/final.mp4") / 1048576 )) MB ($(( $(date +%s) - T0 ))s)"
log_only "assemble: final.mp4 duration=$GOT loudness=$LOUD"
mark_done "$PROJECT" "09-assemble"
stage_end
