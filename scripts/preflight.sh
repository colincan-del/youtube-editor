#!/usr/bin/env bash
# Stage 0 — Preflight.
# Ten-second sanity check before anything expensive runs.
# Fails fast, in plain English, so a bad recording is caught now and not
# forty minutes into a render.
#
# Usage:  ./scripts/preflight.sh [path/to/video.mp4]
#         with no argument, finds the single video in raw/ — or the numbered
#         parts of one (name-part1.mp4, name-part2.mp4 …), checking each

set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/raw"
# For brand_name and project_of. Its log functions are not used here.
source "$ROOT/scripts/lib/common.sh"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
FAILED=0
WARNED=0

fail()  { printf '%s  FAIL%s  %s\n'  "$RED" "$OFF" "$1"; FAILED=1; }
warn()  { printf '%s  WARN%s  %s\n'  "$YEL" "$OFF" "$1"; WARNED=1; }
pass()  { printf '%s  OK  %s  %s\n'  "$GRN" "$OFF" "$1"; }
note()  { printf '%s        %s%s\n'  "$DIM" "$1" "$OFF"; }

echo
echo "Preflight check"
echo "==============="
echo

# ---------------------------------------------------------------- find the video
VIDEO="${1:-}"

if [[ -z "$VIDEO" ]]; then
  # bash 3.2 compatible: newline-separated list, no mapfile
  FOUND=$(find "$RAW" -maxdepth 1 -type f \
      \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.m4v' \) \
      -not -name '._*' | sort)
  COUNT=$(printf '%s\n' "$FOUND" | grep -c . )

  if   [[ "$COUNT" -eq 0 ]]; then
    fail "No video found in raw/"
    note "Drop your Screen Studio export into raw/ and run this again."
    echo; exit 1
  elif [[ "$COUNT" -gt 1 ]] \
       && [[ $(while IFS= read -r f; do project_of "$f"; done <<< "$FOUND" | sort -u | grep -c .) -eq 1 ]] \
       && ! grep -qvE -- '-part[0-9]+\.[^./]+$' <<< "$FOUND"; then
    :   # the numbered parts of one video — checked part by part below
  elif [[ "$COUNT" -gt 1 ]]; then
    fail "Found $COUNT videos in raw/ — I need one video (or the numbered parts of one)."
    while IFS= read -r f; do [[ -n "$f" ]] && note "$(basename "$f")"; done <<< "$FOUND"
    note "Pass the one you mean — ./scripts/preflight.sh raw/<file> — or move the others out."
    echo; exit 1
  fi
  VIDEO=$(printf '%s\n' "$FOUND" | head -1)
fi

if [[ ! -f "$VIDEO" ]]; then
  fail "That file doesn't exist: $VIDEO"
  echo; exit 1
fi

# Every check on the file itself. Run once per part when the video comes in parts,
# and the parts must match each other so stage 1 can join them without re-encoding.
FIRST_SIG=""
check_video() {
  BASE="$(basename "$VIDEO")"
  SIZE_MB=$(( $(stat -f%z "$VIDEO" 2>/dev/null || stat -c%s "$VIDEO") / 1048576 ))

  echo "  Video:  $BASE  (${SIZE_MB} MB)"
  echo

  # ---------------------------------------------------------------- readable?
  if ! ffprobe -v error -show_format "$VIDEO" >/dev/null 2>&1; then
    fail "The file is corrupt or not a video ffmpeg can read."
    note "Try re-exporting from Screen Studio."
    echo; exit 1
  fi
  pass "File opens cleanly"

  # ---------------------------------------------------------------- video stream
  # Parse key=value — ffprobe emits fields in stream order, not the order requested.
  VINFO=$(ffprobe -v error -select_streams v:0 \
          -show_entries stream=width,height,r_frame_rate,codec_name \
          -of default=nw=0 "$VIDEO" 2>/dev/null)

  kv() { grep -m1 "^$1=" <<<"$2" | cut -d= -f2-; }

  if [[ -z "$VINFO" ]]; then
    fail "No video track. This file is audio only."
  else
    W=$(kv width "$VINFO"); H=$(kv height "$VINFO"); VCODEC=$(kv codec_name "$VINFO")
    FPSR=$(kv r_frame_rate "$VINFO")
    FPS=$(awk -F/ '{ if ($2>0) printf "%.0f", $1/$2; else print $1 }' <<<"$FPSR")
    pass "Video track: ${W}x${H} @ ${FPS}fps"
    if (( W < 1280 )); then
      warn "That's a low resolution for YouTube. 1920x1080 or better is expected."
    fi
  fi

  # ---------------------------------------------------------------- duration
  DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VIDEO" 2>/dev/null)
  DUR_INT=${DUR%.*}
  if [[ -z "$DUR_INT" || "$DUR_INT" -lt 1 ]]; then
    fail "Couldn't read the duration — the file may be truncated."
  else
    MINS=$(( DUR_INT / 60 )); SECS=$(( DUR_INT % 60 ))
    pass "Duration: ${MINS}m ${SECS}s"
    if   (( DUR_INT < 60 ));   then note "Short clip — fine for a pipeline test."
    elif (( DUR_INT > 2700 )); then
      warn "Over 45 minutes. Expect a long transcription and a big cut list."
    fi
  fi

  # ---------------------------------------------------------------- audio stream
  AINFO=$(ffprobe -v error -select_streams a:0 \
          -show_entries stream=codec_name,channels,sample_rate \
          -of default=nw=0 "$VIDEO" 2>/dev/null)

  if [[ -z "$AINFO" ]]; then
    fail "No audio track at all. Nothing can be transcribed or cut."
    note "Check that Screen Studio was recording your microphone."
  else
    ACODEC=$(kv codec_name "$AINFO"); CH=$(kv channels "$AINFO"); SR=$(kv sample_rate "$AINFO")
    pass "Audio track: $ACODEC, ${CH}ch, ${SR}Hz"

    # ------------------------------------------------------------ audio levels
    # Sample 60s from 20% in — representative, and fast even on a long file.
    START=$(( DUR_INT / 5 ))
    LEVELS=$(ffmpeg -hide_banner -nostats -v info -ss "$START" -t 60 -i "$VIDEO" \
             -af volumedetect -f null - 2>&1 | grep -E 'mean_volume|max_volume')

    # Take the number after the colon — ffmpeg's filter label ("volumedetect_0")
    # and the memory address both contain digits that a loose regex will grab first.
    MEAN=$(grep mean_volume <<<"$LEVELS" | sed -E 's/.*mean_volume: *(-?[0-9.]+).*/\1/' | head -1)
    MAX=$(grep  max_volume  <<<"$LEVELS" | sed -E 's/.*max_volume: *(-?[0-9.]+).*/\1/'  | head -1)

    if [[ -z "$MEAN" ]]; then
      warn "Couldn't measure the audio levels. Listen to it yourself before continuing."
    else
      printf '         mean %s dB · peak %s dB\n' "$MEAN" "$MAX"
      SILENT=$(awk -v m="$MEAN" 'BEGIN{print (m < -50) ? 1 : 0}')
      QUIET=$(awk  -v m="$MEAN" 'BEGIN{print (m < -32) ? 1 : 0}')
      CLIP=$(awk   -v x="$MAX"  'BEGIN{print (x > -0.5) ? 1 : 0}')

      if   (( SILENT )); then
        fail "The audio is essentially silent. The wrong input was almost certainly recorded."
        note "Check your microphone selection in Screen Studio and re-record."
      elif (( QUIET )); then
        warn "The audio is very quiet (mean ${MEAN} dB). It will still transcribe, but check it."
      else
        pass "Audio levels look healthy"
      fi

      if (( CLIP )); then
        warn "Peaks are hitting ${MAX} dB — the mic may be clipping and distorting."
      fi
    fi
  fi

  SIG="${W:-?}x${H:-?} ${FPSR:-?}fps ${VCODEC:-?} · ${ACODEC:-?} ${SR:-?}Hz ${CH:-?}ch"
  if [[ -z "$FIRST_SIG" ]]; then
    FIRST_SIG="$SIG"
  elif [[ "$SIG" != "$FIRST_SIG" ]]; then
    fail "This part was exported differently from part 1: $SIG, not $FIRST_SIG."
    note "Export every part with the same Screen Studio settings so they join cleanly."
  fi
}

PARTS="$(parts_of "$VIDEO")" || { fail "The parts of $(project_of "$VIDEO") can't be joined (see above)."; echo; exit 1; }
IFS=$'\n'; set -f; PART_LIST=($PARTS); set +f; unset IFS
TOTAL=0; i=0
for VIDEO in "${PART_LIST[@]}"; do
  i=$((i + 1))
  (( ${#PART_LIST[@]} > 1 )) && echo "  ---- Part $i of ${#PART_LIST[@]}"
  check_video
  TOTAL=$(( TOTAL + ${DUR_INT:-0} ))
  echo
done
if (( ${#PART_LIST[@]} > 1 )); then
  pass "${#PART_LIST[@]} parts, $(( TOTAL / 3600 ))h $(( TOTAL % 3600 / 60 ))m in all — stage 1 joins them in this order"
fi
STEM="$(project_of "$VIDEO")"

# ---------------------------------------------------------------- outline
# Look next to the video first, then fall back to raw/ — so an explicit path
# outside raw/ still finds its own outline.
VDIR="$(cd "$(dirname "$VIDEO")" && pwd)"
OUTLINE=""
for dir in "$VDIR" "$RAW"; do
  for ext in md txt markdown; do
    [[ -f "$dir/$STEM.$ext" ]] && OUTLINE="$dir/$STEM.$ext" && break 2
  done
done

if [[ -z "$OUTLINE" ]]; then
  fail "No outline file found. Expected ${STEM}.md next to the video."
  note "Without it the system can't tell an essential section from a tangent."
  note "See raw/README.md for the format. Five to fifteen bullets is enough."
else
  BULLETS=$(grep -cE '^\s*[-*]' "$OUTLINE" 2>/dev/null || echo 0)
  if (( BULLETS < 3 )); then
    warn "Outline found but only $BULLETS bullet(s). More detail means better cuts."
  else
    pass "Outline: $(basename "$OUTLINE") — $BULLETS bullets"
  fi
fi

# ---------------------------------------------------------------- brand files
# The project's brand if stage 1 has run, otherwise brands/active.
BNAME="$(brand_name "$(project_of "$VIDEO")")"
BDIR="$BRANDS/$BNAME"
for f in brand.json glossary.md fonts/fonts.css; do
  [[ -f "$BDIR/$f" ]] || fail "Brand '$BNAME' is missing brands/$BNAME/$f"
done
[[ $FAILED -eq 0 ]] && pass "Brand: $BNAME"

# staleness — only for files that carry a date. recheck_after is a due date:
# warn once it has passed. A file with only `updated:` is allowed 30 days.
for f in "$BDIR"/brand-kit.md "$BDIR"/icp.md; do
  [[ -f "$f" ]] || continue
  rel="${f#$ROOT/}"
  LINE=$(grep -m1 -E '^recheck_after:' "$f" 2>/dev/null)
  [[ -z "$LINE" ]] && LINE=$(grep -m1 -E '^updated:' "$f" 2>/dev/null)
  if [[ -z "$LINE" ]]; then
    warn "$rel has no recheck_after date, so there is no way to tell if it's stale."
    continue
  fi
  FIELD="${LINE%%:*}"
  WHEN=$(echo "$LINE" | awk '{print $2}' | tr -d '"')
  WHEN_S=$(date -j -f "%Y-%m-%d" "$WHEN" +%s 2>/dev/null)
  if [[ -z "$WHEN_S" ]]; then
    warn "$rel has an unreadable $FIELD date ('$WHEN')."
    continue
  fi
  PAST_DAYS=$(( ( $(date +%s) - WHEN_S ) / 86400 ))
  if [[ "$FIELD" == "recheck_after" ]]; then
    (( PAST_DAYS > 0 )) && warn "$rel was due for a check against the brand's website on $WHEN — ${PAST_DAYS} days ago."
  else
    (( PAST_DAYS > 30 )) && warn "$rel hasn't been checked against the brand's website in ${PAST_DAYS} days."
  fi
done

# ---------------------------------------------------------------- verdict
echo
if (( FAILED )); then
  printf '%sPreflight failed.%s Fix the items above before running the edit.\n\n' "$RED" "$OFF"
  exit 1
elif (( WARNED )); then
  printf '%sPreflight passed with warnings.%s Safe to continue if you accept them.\n\n' "$YEL" "$OFF"
  exit 0
else
  printf '%sPreflight passed.%s Ready to edit.\n\n' "$GRN" "$OFF"
  exit 0
fi
