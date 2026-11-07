#!/usr/bin/env bash
# Stage 10 — Package.
# Everything that goes up alongside the video. Two passes, like stages 5 and 6:
#
#   pass 1   → work/<project>/package-brief.md
#            (Claude writes package.proposal.json — format in scripts/package.py)
#   pass 2   → work/<project>/package/
#              title.txt · description.txt · captions.srt
#              thumbnail.png      1280×720, Hyperframes still
#              linkedin-clip.mp4  1080×1080, burned-in captions
#
# Usage:  ./scripts/10-package.sh [project] [--force]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
HF="$ROOT/tools/hyperframes/node_modules/.bin/hyperframes"

PROJECT=""
for a in "$@"; do case "$a" in -*) ;; *) PROJECT="$a" ;; esac; done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi
DIR="$WORK/$PROJECT"; PKG="$DIR/package"
PROPOSAL="$DIR/package.proposal.json"

stage_begin "10 — package"
log "Project: $PROJECT"
stage_done "$PROJECT" "09-assemble" || die "There's no finished video yet — run ./scripts/09-assemble.sh."
if stage_done "$PROJECT" "10-package" && (( ! FORCE )) && ! [[ "$PROPOSAL" -nt "$PKG/title.txt" ]]; then
  log_ok "Already done — work/$PROJECT/package/"
  stage_end; exit 0
fi

"$PY" "$ROOT/scripts/package.py" prepare "$DIR" > /dev/null || die "Couldn't write the package brief."
if [[ ! -f "$PROPOSAL" ]]; then
  log "Waiting for the package proposal"
  log_note "Read work/$PROJECT/package-brief.md, write work/$PROJECT/package.proposal.json,"
  log_note "then run this stage again."
  stage_end; exit 0
fi

OUT=$("$PY" "$ROOT/scripts/package.py" finalize "$DIR") \
  || die "The package proposal can't be used as written — see above."
read -r NCUES FRAME_AT LA LB NWARN <<< "$(printf '%s\n' "$OUT" | head -1)"
while IFS= read -r line; do [[ "$line" == WARN* ]] && log_warn "${line#WARN }"; done <<< "$OUT"
log_ok "title.txt · description.txt · captions.srt ($NCUES cues)"

# ---------------------------------------------------------------- thumbnail
BG="$(brand_get "$(brand_dir "$PROJECT")" 'b["colors"]["ground"]')"
ffmpeg -hide_banner -v error -y -ss "$FRAME_AT" -i "$DIR/clean.mov" -frames:v 1 -q:v 2 \
  "$PKG/thumbnail/frame.jpg" || die "Couldn't take the thumbnail frame."
"$HF" snapshot "$PKG/thumbnail" --at 0.5 --no-end -o "$PKG/thumbnail/shots" > "$PKG/thumbnail/render.log" 2>&1 \
  || die "Hyperframes couldn't render the thumbnail — see work/$PROJECT/package/thumbnail/render.log"
SHOT=$(find "$PKG/thumbnail/shots" -name '*.png' | head -1)
[[ -n "$SHOT" ]] || die "The thumbnail snapshot produced no image."
ffmpeg -hide_banner -v error -y -i "$SHOT" -vf "scale=1280:720" "$PKG/thumbnail.png" \
  || die "Couldn't size the thumbnail."
log_ok "thumbnail.png — frame from $(hms "$FRAME_AT")"

# ---------------------------------------------------------------- LinkedIn
# Square. The footage sits full width at y=264 on the brand ground; the graphics
# above and below it are a transparent Hyperframes overlay laid out to match
# (templates/graphics/linkedin.html).
LDUR=$(awk -v a="$LA" -v b="$LB" 'BEGIN{printf "%.3f", b-a}')
"$HF" render "$PKG/linkedin" --format webm --fps 30 -o "$PKG/linkedin/overlay.webm" --quiet \
  > "$PKG/linkedin/render.log" 2>&1 \
  || die "Hyperframes couldn't render the LinkedIn graphics — see work/$PROJECT/package/linkedin/render.log"
ffmpeg -hide_banner -nostats -v error -y -ss "$LA" -t "$LDUR" -i "$DIR/clean.mov" \
  -c:v libvpx-vp9 -i "$PKG/linkedin/overlay.webm" \
  -filter_complex "color=c=${BG}:s=1080x1080:r=30:d=${LDUR}[bg];[0:v]scale=1080:-2,fps=30[v];[bg][v]overlay=0:264[base];[base][1:v]overlay=0:0:format=auto[out]" \
  -map '[out]' -map 0:a -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart \
  "$PKG/linkedin-clip.mp4" || die "Couldn't build the LinkedIn clip."
log_ok "linkedin-clip.mp4 — $(hms "$LDUR") from $(hms "$LA")"

mark_done "$PROJECT" "10-package"
stage_end
