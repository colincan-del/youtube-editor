#!/usr/bin/env bash
# Shared plumbing for every pipeline stage.
# Sourced, never executed. Bash 3.2 safe — no mapfile, no associative arrays.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="$ROOT/raw"
WORK="$ROOT/work"
OUT="$ROOT/output"
LOGDIR="$ROOT/logs"
# Outside the project on purpose: the Desktop syncs to iCloud, and "Optimize Mac
# Storage" offloads the venv's files to the cloud. WhisperX then hangs silently
# importing torch, one file download at a time. Rebuild with:
#   uv venv --python 3.11 "$VENV" && uv pip install --python "$VENV/bin/python" -r scripts/requirements.txt
BRANDS="$ROOT/brands"
VENV="${OVERVUE_VENV:-$HOME/.local/share/overvue-video/venv}"
PY="$VENV/bin/python"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'

mkdir -p "$LOGDIR"
RUNLOG="$LOGDIR/run.log"

# ---------------------------------------------------------------- logging
# Every line lands in logs/run.log with a timestamp. Colin has to be able to
# read this without a developer, so no JSON and no stack traces.

_ts() { date '+%Y-%m-%d %H:%M:%S'; }

_write() { printf '%s  %s\n' "$(_ts)" "$1" >> "$RUNLOG"; }

log()      { printf '  %s\n' "$1"; _write "$1"; }
log_ok()   { printf '%s  OK  %s  %s\n' "$GRN" "$OFF" "$1"; _write "OK    $1"; }
log_warn() { printf '%s  WARN%s  %s\n' "$YEL" "$OFF" "$1"; _write "WARN  $1"; }
log_err()  { printf '%s  FAIL%s  %s\n' "$RED" "$OFF" "$1"; _write "FAIL  $1"; }
log_note() { printf '%s        %s%s\n' "$DIM" "$1" "$OFF"; _write "      $1"; }
log_only() { _write "$1"; }   # goes to the file, not the terminal

die() { log_err "$1"; echo; exit 1; }

# ---------------------------------------------------------------- stage timing
STAGE_NAME=""
STAGE_START=0

stage_begin() {
  STAGE_NAME="$1"; STAGE_START=$(date +%s)
  echo
  echo "Stage $STAGE_NAME"
  printf '%s\n' "$(printf '=%.0s' $(seq 1 $(( ${#STAGE_NAME} + 6 ))))"
  echo
  _write "----- stage $STAGE_NAME : begin -----"
}

stage_end() {
  local secs=$(( $(date +%s) - STAGE_START ))
  _write "----- stage $STAGE_NAME : done in ${secs}s -----"
  printf '\n%sDone%s in %ss.\n\n' "$GRN" "$OFF" "$secs"
}

# ---------------------------------------------------------------- stage markers
# A failed run resumes from the last completed stage. Each stage drops a marker
# on success; each stage checks for its own before doing the work again.

marker_path() { printf '%s/%s/stages/%s.done' "$WORK" "$1" "$2"; }

stage_done() { [[ -f "$(marker_path "$1" "$2")" ]]; }

mark_done() {
  mkdir -p "$WORK/$1/stages"
  printf 'completed %s\n' "$(_ts)" > "$(marker_path "$1" "$2")"
}

# Honour --force / -f to redo a stage that already has a marker.
FORCE=0
for _a in "$@"; do
  [[ "$_a" == "--force" || "$_a" == "-f" ]] && FORCE=1
done

# skip_if_done <project> <stage>  → returns 0 when the caller should skip
skip_if_done() {
  if stage_done "$1" "$2" && (( ! FORCE )); then
    log_ok "Already done — skipping. Use --force to redo it."
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------- project name
# One video in raw/ → the project is its filename stem. A long recording can come
# as numbered parts of one video instead — walkthrough-part1.mp4, walkthrough-part2.mp4
# … with one outline, walkthrough.md — and stage 1 joins them into one source.

find_raw_video() {
  local found count
  found=$(find "$RAW" -maxdepth 1 -type f \
      \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.m4v' \) \
      -not -name '._*' | sort)
  count=$(printf '%s\n' "$found" | grep -c .)
  if [[ "$count" -eq 0 ]]; then
    log_err "No video found in raw/"
    log_note "Drop your Screen Studio export into raw/ and run this again."
    return 1
  elif [[ "$count" -gt 1 ]]; then
    # Several files are fine when they are all parts of one video.
    if [[ $(while IFS= read -r f; do project_of "$f"; done <<< "$found" | sort -u | grep -c .) -eq 1 ]] \
       && ! grep -qvE -- '-part[0-9]+\.[^./]+$' <<< "$found"; then
      parts_of "$(head -1 <<< "$found")" | head -1
      return
    fi
    log_err "Found $count videos in raw/ — I need one video (or the numbered parts of one)."
    log_note "Name the project — ./scripts/01-copy.sh <name> — or move the others out of raw/."
    return 1
  fi
  printf '%s\n' "$found" | head -1
}

# The project name for a video path: its stem, without any -partN.
project_of() { local b; b="$(basename "$1")"; printf '%s\n' "${b%.*}" | sed -E 's/-part[0-9]+$//'; }

# parts_of <video>  → every part of the video it belongs to, in part order, one per
# line; just the video itself if it isn't a part. Fails if a part is missing.
parts_of() {
  local b; b="$(basename "$1")"
  if [[ ! "${b%.*}" =~ -part[0-9]+$ ]]; then printf '%s\n' "$1"; return; fi
  find "$(dirname "$1")" -maxdepth 1 -type f -name "$(project_of "$1")-part*" -not -name '._*' \
      \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.m4v' \) \
    | grep -E -- '-part[0-9]+\.[^./]+$' \
    | awk '{ n = $0; sub(/\.[^.]*$/, "", n); sub(/.*-part/, "", n); print n "\t" $0 }' \
    | sort -n \
    | awk -F'\t' '$1 != NR { print "Part " NR " is missing — the parts must be numbered 1, 2, 3 … with no gaps." > "/dev/stderr"; exit 1 }
                  { print $2 }'
}

# ---------------------------------------------------------------- guards
# Hard rule 1. Nothing in this system may ever write inside raw/.
assert_not_raw() {
  local p; p="$(cd "$(dirname "$1")" 2>/dev/null && pwd)/$(basename "$1")"
  case "$p" in
    "$RAW"/*) die "Refusing to write inside raw/ — it is read-only. ($1)" ;;
  esac
}

# ---------------------------------------------------------------- brand
# Every brand-specific file — colours, fonts, glossary, voice rules, the
# held-back client list — lives in brands/<name>/. Stage 1 records which brand
# a project uses in work/<project>/brand.txt, so a project keeps its brand even
# if brands/active changes later. Before stage 1, brands/active is used.

brand_name() {
  local b=""
  [[ -n "${1:-}" && -f "$WORK/$1/brand.txt" ]] && b="$(tr -d ' \n' < "$WORK/$1/brand.txt")"
  [[ -z "$b" && -f "$BRANDS/active" ]] && b="$(tr -d ' \n' < "$BRANDS/active")"
  printf '%s\n' "${b:-default}"
}

brand_dir() {
  local name; name="$(brand_name "${1:-}")"
  [[ -f "$BRANDS/$name/brand.json" ]] || die "Brand '$name' has no brands/$name/brand.json."
  printf '%s\n' "$BRANDS/$name"
}

# brand_get <brand-dir> <python expression on b>  e.g. brand_get "$B" 'b["colors"]["ground"]'
brand_get() { "$PY" -c "import json,sys;b=json.load(open(sys.argv[1]));print($2)" "$1/brand.json"; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "$1 is not installed."; }

# seconds → 0:00:00.0 for a human, and for ffmpeg
hms() { awk -v s="$1" 'BEGIN{h=int(s/3600);m=int((s%3600)/60);printf "%d:%02d:%05.2f",h,m,s-h*3600-m*60}'; }
