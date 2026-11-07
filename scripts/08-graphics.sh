#!/usr/bin/env bash
# Stage 8 — Graphics, every chapter.
# The look Colin approved at checkpoint 2, rendered over each whole chapter.
# A chapter re-renders only if something it depends on changed since —
# its video, the chapter list, the graphics plan, the template or the brand.
#
#   → work/<project>/graphics/NN/composite.mov
#
# Usage:  ./scripts/08-graphics.sh [project] [--force] [--chapter N]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/render-chapter.sh"

PROJECT=""; ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --chapter) shift; ONLY="${1:-}" ;;
    -*) ;;
    *)  PROJECT="$1" ;;
  esac
  shift
done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi
DIR="$WORK/$PROJECT"

stage_begin "8 — graphics"
log "Project: $PROJECT"
stage_done "$PROJECT" "07-sample-approved" \
  || die "Colin hasn't approved the look yet (checkpoint 2) — see work/$PROJECT/sample.md."

NCH=$("$PY" -c "import json;print(len(json.load(open('$DIR/chapters.json'))['chapters']))")
BDIR="$(brand_dir "$PROJECT")"
DEPS="$DIR/chapters.json $DIR/graphics.plan.json $ROOT/templates/graphics/chapter.html $ROOT/scripts/graphics.py $BDIR/brand.json"

T_ALL=$(date +%s); DONE=0; SKIP=0
for (( n=1; n<=NCH; n++ )); do
  [[ -n "$ONLY" && "$n" != "$ONLY" ]] && continue
  NN=$(printf '%02d' "$n"); OUTF="$DIR/graphics/$NN/composite.mov"
  STALE=0
  if [[ ! -f "$OUTF" ]] || (( FORCE )); then STALE=1; else
    for d in $DEPS "$DIR/chapters/$NN.mov"; do [[ "$d" -nt "$OUTF" ]] && STALE=1; done
  fi
  if (( ! STALE )); then SKIP=$((SKIP+1)); continue; fi
  log "Chapter $n of $NCH"
  # render_chapter rebuilds graphics/NN/, so render aside and move into place.
  render_chapter "$PROJECT" "$n" "$DIR/.composite-$NN.mov"
  mv "$DIR/.composite-$NN.mov" "$OUTF"
  log_ok "Chapter $n — ${RENDER_SECS}s"
  DONE=$((DONE+1))
done

log_ok "$DONE chapter(s) rendered, $SKIP already up to date ($(( $(date +%s) - T_ALL ))s)"
[[ -z "$ONLY" ]] && mark_done "$PROJECT" "08-graphics"
stage_end
