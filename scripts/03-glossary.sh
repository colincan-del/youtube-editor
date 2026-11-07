#!/usr/bin/env bash
# Stage 3 — Glossary correction.
# Runs over the transcript before anything downstream reads it. If this stage
# is skipped, "OverVue" ships as "Overview" in the captions and description.
#
# Usage:  ./scripts/03-glossary.sh [project] [--force]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROJECT="${1:-}"
[[ "$PROJECT" == --* || "$PROJECT" == -f ]] && PROJECT=""
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi

DIR="$WORK/$PROJECT"
BRAND="$(brand_dir "$PROJECT")"
GLOSS="$BRAND/glossary.md"

stage_begin "3 — glossary"
log "Project: $PROJECT"

[[ -f "$DIR/transcript.json" ]] || die "No transcript yet — run ./scripts/02-transcribe.sh first."
[[ -f "$GLOSS" ]] || die "${GLOSS#$ROOT/} is missing. Nothing can be corrected without it."

if skip_if_done "$PROJECT" "03-glossary"; then
  log_note "work/$PROJECT/transcript.corrected.json"
  stage_end; exit 0
fi

log_only "glossary: $GLOSS ($(grep -m1 -E '^updated:' "$GLOSS" | awk '{print $2}'))"

RESULT=$("$PY" "$ROOT/scripts/glossary.py" "$GLOSS" "$DIR/transcript.json" "$DIR") || {
  log_err "The glossary pass failed."
  log_note "If it says the table structure changed, fix ${GLOSS#$ROOT/} — the"
  log_note "system stops rather than guessing which words are wrong."
  echo; exit 1
}
read -r CHANGES RULES FLAGGED <<< "$RESULT"

log_ok "$RULES rules loaded, $CHANGES corrections made"
if [[ "${FLAGGED:-0}" -gt 0 ]]; then
  log_warn "$FLAGGED of them came from ambiguous one-word rules."
  log_note "Listed under 'Worth a glance' in work/$PROJECT/glossary-report.md"
fi
log_note "Report: work/$PROJECT/glossary-report.md"
log_only "glossary: rules=$RULES corrections=$CHANGES flagged=$FLAGGED"

mark_done "$PROJECT" "03-glossary"
stage_end
