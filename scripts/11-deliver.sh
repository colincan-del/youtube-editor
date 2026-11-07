#!/usr/bin/env bash
# Stage 11 — Deliver.  CHECKPOINT 3.
# Writes the folder Colin uploads from. Never publishes (hard rule 10) and
# never overwrites an existing output/<project>/ without --overwrite (rule 11).
#
#   → output/<project>/  final.mp4 · thumbnail.png · title.txt · description.txt
#                        captions.srt · linkedin-clip.mp4 · edit-notes.md · run.log
#
# Usage:  ./scripts/11-deliver.sh [project] [--overwrite]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROJECT=""; OVERWRITE=0
for a in "$@"; do case "$a" in --overwrite) OVERWRITE=1 ;; -*) ;; *) PROJECT="$a" ;; esac; done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi
DIR="$WORK/$PROJECT"; DEST="$OUT/$PROJECT"

stage_begin "11 — deliver"
log "Project: $PROJECT"
stage_done "$PROJECT" "10-package" || die "The package isn't built yet — run ./scripts/10-package.sh."
if [[ -d "$DEST" ]]; then
  (( OVERWRITE )) || die "output/$PROJECT/ already exists. Re-run with --overwrite once Colin says to replace it."
  log_warn "Replacing output/$PROJECT/ — Colin confirmed."
  rm -rf "$DEST"
fi
mkdir -p "$DEST"

cp "$DIR/final.mp4" "$DEST/"
for f in title.txt description.txt captions.srt thumbnail.png linkedin-clip.mp4; do
  cp "$DIR/package/$f" "$DEST/" || die "package/$f is missing."
done

# What was cut, and what was deliberately left alone — the evidence that the
# hard rules held.
{
  echo "# Edit notes — $PROJECT"; echo
  echo "Brand: $(brand_name "$PROJECT")"; echo
  echo "## Content cuts (checkpoint 1)"; echo
  sed -n '/^| #/,/^$/p' "$DIR/cutlist.md"
  echo "## Chapters"; echo
  sed -n '/^| #/,/^$/p' "$DIR/chapters.md"
  echo "## Cleanup (stage 4) — including cuts not made"; echo
  sed '1{/^#/d;}' "$DIR/cleanup-notes.md"
} > "$DEST/edit-notes.md"

log_ok "Delivered to output/$PROJECT/"
_write "CHECKPOINT 3 — package waiting for Colin: output/$PROJECT/"
cp "$RUNLOG" "$DEST/run.log"
echo
printf '%s  CHECKPOINT 3%s  Everything is in output/%s/ — Colin reviews, then uploads.\n' "$YEL" "$OFF" "$PROJECT"
stage_end
