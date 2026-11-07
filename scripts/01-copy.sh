#!/usr/bin/env bash
# Stage 1 — Copy.
# raw/ is read-only. Everything downstream works on work/<project>/source.mp4.
# This stage exists only so that no other stage ever has to think about it.
#
# Usage:  ./scripts/01-copy.sh [path/to/video.mp4 | project] [--brand name] [--length 12-18|full] [--force]
#
# --brand picks brands/<name>/ for this project; without it, brands/active.
# --length is the finished video's target in minutes, or "full" for no target —
#   a product walkthrough that keeps everything that works. Default 12-18.
#
# A video in numbered parts (name-part1.mp4, name-part2.mp4 …, one name.md outline)
# is joined here, in part order, without re-encoding → one source.mp4, and
# work/<project>/parts.txt records where each part starts.

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# Takes a path, or — so that resuming a run reads the same as every other
# stage — a project name.
VIDEO=""; BRAND_ARG=""; LENGTH_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --brand)    shift; BRAND_ARG="${1:-}" ;;
    --brand=*)  BRAND_ARG="${1#--brand=}" ;;
    --length)   shift; LENGTH_ARG="${1:-}" ;;
    --length=*) LENGTH_ARG="${1#--length=}" ;;
    -*)        ;;
    *)         VIDEO="$1" ;;
  esac
  shift
done
if [[ -n "$BRAND_ARG" && ! -f "$BRANDS/$BRAND_ARG/brand.json" ]]; then
  echo "No brand called '$BRAND_ARG' — the brands are: $(ls "$BRANDS" | grep -v active | tr '\n' ' ')"; exit 1
fi
if [[ -n "$LENGTH_ARG" && ! "$LENGTH_ARG" =~ ^(full|[0-9]+-[0-9]+)$ ]]; then
  echo "--length takes minutes, like 12-18 or 40-60, or \"full\" for no target — not '$LENGTH_ARG'."; exit 1
fi
if [[ -z "$VIDEO" ]]; then
  VIDEO="$(find_raw_video)" || exit 1
elif [[ ! -f "$VIDEO" ]]; then
  NAME="$VIDEO"; VIDEO=""
  for dir in "$RAW" "$ROOT/fixtures"; do
    for ext in mp4 mov mkv m4v MP4 MOV; do
      [[ -f "$dir/$NAME.$ext" ]] && VIDEO="$dir/$NAME.$ext" && break 2
      [[ -f "$dir/$NAME-part1.$ext" ]] && VIDEO="$dir/$NAME-part1.$ext" && break 2
    done
  done
  if [[ -z "$VIDEO" ]]; then
    if [[ -f "$WORK/$NAME/source.mp4" ]]; then
      stage_begin "1 — copy"
      log "Project: $NAME"
      log_ok "Already done — skipping. Use --force to redo it."
      log_note "The original is no longer in raw/, so there is nothing to re-copy."
      stage_end; exit 0
    fi
    die "No video called '$NAME' in raw/, and no path by that name either."
  fi
fi

PROJECT="$(project_of "$VIDEO")"
DEST="$WORK/$PROJECT/source.mp4"

stage_begin "1 — copy"
log "Project: $PROJECT"

assert_not_raw "$DEST"

# The brand is fixed for the project here. Re-running with --brand changes it.
mkdir -p "$WORK/$PROJECT"
if [[ -n "$BRAND_ARG" ]]; then
  printf '%s\n' "$BRAND_ARG" > "$WORK/$PROJECT/brand.txt"
elif [[ ! -f "$WORK/$PROJECT/brand.txt" ]]; then
  brand_name "" > "$WORK/$PROJECT/brand.txt"
fi
log "Brand: $(brand_name "$PROJECT")"
log_only "brand: $(brand_name "$PROJECT")"

# The length target is fixed here too (cutlist.py reads it). No file means 12-18.
[[ -n "$LENGTH_ARG" ]] && printf '%s\n' "$LENGTH_ARG" > "$WORK/$PROJECT/length.txt"
LENGTH="$(cat "$WORK/$PROJECT/length.txt" 2>/dev/null || echo 12-18)"
if [[ "$LENGTH" == full ]]; then log "Length: full walkthrough — no length target"
else log "Length target: $LENGTH minutes"; fi

if skip_if_done "$PROJECT" "01-copy"; then
  log_note "$DEST"
  stage_end; exit 0
fi

mkdir -p "$WORK/$PROJECT"

PARTS="$(parts_of "$VIDEO")" || die "The parts of $PROJECT can't be joined — see above."
NPARTS=$(grep -c . <<< "$PARTS")

if (( NPARTS == 1 )); then
  SRC_SIZE=$(stat -f%z "$VIDEO" 2>/dev/null || stat -c%s "$VIDEO")
  log "Copying $(basename "$VIDEO") — $(( SRC_SIZE / 1048576 )) MB"
  log_only "source: $VIDEO"

  cp "$VIDEO" "$DEST" || die "The copy failed. Check you have disk space free."

  DST_SIZE=$(stat -f%z "$DEST" 2>/dev/null || stat -c%s "$DEST")
  [[ "$SRC_SIZE" == "$DST_SIZE" ]] || die "The copy came out a different size. Disk may be full."
else
  # Stream copy: no quality lost and minutes, not hours, for a long recording. It
  # needs every part exported the same way, which preflight checks.
  log "Joining $NPARTS parts"
  LIST="$WORK/$PROJECT/.parts.txt"; : > "$LIST"; : > "$WORK/$PROJECT/parts.txt"
  AT=0; n=0
  while IFS= read -r p; do
    n=$((n + 1))
    D=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$p") \
      || die "Part $n ($(basename "$p")) won't open — re-export it."
    log_note "Part $n — $(basename "$p"), $(hms "$D"), starts at $(hms "$AT")"
    log_only "source: $p"
    printf '%s\t%s\t%s\n' "$n" "$AT" "$(basename "$p")" >> "$WORK/$PROJECT/parts.txt"
    printf "file '%s'\n" "$(sed "s/'/'\\\\''/g" <<< "$p")" >> "$LIST"
    AT=$(awk -v a="$AT" -v d="$D" 'BEGIN{printf "%.3f", a + d}')
  done <<< "$PARTS"
  ffmpeg -hide_banner -nostdin -nostats -v error -y -f concat -safe 0 -i "$LIST" -c copy \
    -movflags +faststart "$DEST" || die "Couldn't join the parts. Check you have disk space free."
  rm -f "$LIST"
  GOT=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$DEST")
  awk -v a="$GOT" -v b="$AT" -v n="$NPARTS" 'BEGIN{d=a-b; if (d<0) d=-d; exit !(d < 0.5 + 0.1*n)}' \
    || die "The joined video is $(hms "$GOT") but the parts add up to $(hms "$AT") — something was dropped."
  log_ok "Joined: $(hms "$GOT"), $(( $(stat -f%z "$DEST") / 1048576 )) MB"
fi

# The outline travels with the video — every later stage needs it and should not
# have to go looking in raw/ for it. A video in parts has one outline, name.md.
STEM="$PROJECT"
VDIR="$(cd "$(dirname "$VIDEO")" && pwd)"
for dir in "$VDIR" "$RAW"; do
  for ext in md txt markdown; do
    if [[ -f "$dir/$STEM.$ext" ]]; then
      cp "$dir/$STEM.$ext" "$WORK/$PROJECT/outline.md"
      log_ok "Outline copied alongside it"
      break 2
    fi
  done
done
[[ -f "$WORK/$PROJECT/outline.md" ]] || log_warn "No outline copied — preflight should have caught this."

log_ok "Copied to work/$PROJECT/source.mp4"
log_note "raw/ untouched"

mark_done "$PROJECT" "01-copy"
stage_end
