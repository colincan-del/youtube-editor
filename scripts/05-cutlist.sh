#!/usr/bin/env bash
# Stage 5 — Cut list.  CHECKPOINT 1.
# Proposes content cuts to bring the rough cut to 12-18 minutes. Renders
# nothing. The judgement in the middle is Claude's, so this runs in two passes:
#
#   pass 1   lays the rough cut out as sentences, marks where a cut may land
#            → work/<project>/sentences.md
#            (Claude reads it against the outline, writes cutlist.proposal.json)
#   pass 2   turns the proposal into the table Colin approves    ← stops here
#            → work/<project>/cutlist.md
#   --approve [--keep 2,5]   records Colin's answer and locks the cut
#            → work/<project>/locked.json
#
# Usage:  ./scripts/05-cutlist.sh [project] [--force]
#         ./scripts/05-cutlist.sh [project] --approve [--keep 2,5]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROJECT=""; APPROVE=0; KEEP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --approve)  APPROVE=1 ;;
    --keep)     shift; KEEP="${1:-}" ;;
    --keep=*)   KEEP="${1#--keep=}" ;;
    --force|-f) ;;
    -*)         echo "Unknown option: $1"; exit 2 ;;
    *)          PROJECT="$1" ;;
  esac
  shift
done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi

DIR="$WORK/$PROJECT"
PROPOSAL="$DIR/cutlist.proposal.json"
CUTLIST="$DIR/cutlist.json"

stage_begin "5 — cut list"
log "Project: $PROJECT"

stage_done "$PROJECT" "04-cleanup" && [[ -f "$DIR/cleanup.json" ]] \
  || die "Stage 4 hasn't finished for '$PROJECT' — run ./scripts/04-cleanup.sh first."

# Writes stay in work/. Every path below is inside it; this proves it.
assert_not_raw "$DIR/cutlist.md"

log_cut_lines() {
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && log_only "  ${line#CUT }"
  done <<< "$(printf '%s\n' "$1" | grep '^CUT ')"
}

# ---------------------------------------------------------------- approve
if (( APPROVE )); then
  [[ -f "$CUTLIST" ]] && stage_done "$PROJECT" "05-cutlist" \
    || die "There is no cut list to approve yet. Run this stage without --approve first."
  OUT=$("$PY" "$ROOT/scripts/cutlist.py" approve "$DIR" --keep "$KEEP") \
    || die "Nothing was locked — see the reason above."
  read -r NAPP NKEPT LOCKDUR NKEEPS <<< "$(printf '%s\n' "$OUT" | head -1)"
  log_only "cutlist: approved by Colin — made=$NAPP kept_in=$NKEPT${KEEP:+ (kept: $KEEP)}"
  log_cut_lines "$OUT"
  log_ok "Approved — $NAPP cut(s) made, $NKEPT kept in"
  log_ok "Locked cut: $(hms "$LOCKDUR") in $NKEEPS pieces → work/$PROJECT/locked.json"
  mark_done "$PROJECT" "05-cutlist-approved"
  stage_end; exit 0
fi

# ---------------------------------------------------------------- resume
# Done means the list is waiting on Colin. A newer proposal means Claude has
# revised it, so the table is rebuilt without needing --force.
if stage_done "$PROJECT" "05-cutlist" && (( ! FORCE )) \
   && ! [[ -f "$PROPOSAL" && "$PROPOSAL" -nt "$CUTLIST" ]]; then
  if stage_done "$PROJECT" "05-cutlist-approved"; then
    log_ok "Already approved — the locked cut is work/$PROJECT/locked.json"
  else
    log_ok "The cut list is ready and waiting on Colin: work/$PROJECT/cutlist.md"
    log_note "Approve with --approve, or --approve --keep 2,5 to keep cuts 2 and 5 in."
  fi
  stage_end; exit 0
fi

# ---------------------------------------------------------------- pass 1
# The held-back client list is optional per brand. If the brand has one, it
# must parse (hard rule 4); if it has none, no names are flagged.
ICP="$(brand_dir "$PROJECT")/icp.md"
if [[ -f "$ICP" ]]; then
  log_only "icp: ${ICP#$ROOT/} ($(grep -m1 -E '^(verified|updated):' "$ICP" | awk '{print $2}'))"
else
  log_note "Brand '$(brand_name "$PROJECT")' has no icp.md — no held-back names to look for."
  ICP="-"
fi
log "Laying the rough cut out as sentences"
OUT=$("$PY" "$ROOT/scripts/cutlist.py" prepare "$DIR" "$ICP") \
  || die "Couldn't lay out the sentences — see the reason above."
read -r NSENT ROUGH LEGAL NBOUND NMENT HASOUTLINE <<< "$OUT"
log_ok "$NSENT sentences in a $(hms "$ROUGH") rough cut"
log_note "$LEGAL of the $NBOUND gaps between them are clean places to cut"
log_only "cutlist: sentences=$NSENT legal_boundaries=$LEGAL/$NBOUND held_back_mentions=$NMENT"
[[ "$HASOUTLINE" -eq 0 ]] && log_warn "No outline in work/$PROJECT/ — nothing to judge a tangent against."
[[ "$NMENT" -gt 0 ]] && log_warn "$NMENT mention(s) of a name on the held-back list — see sentences.md"

if [[ ! -f "$PROPOSAL" ]]; then
  log "Waiting for the cut proposal"
  log_note "Read work/$PROJECT/sentences.md against the outline, write"
  log_note "work/$PROJECT/cutlist.proposal.json, then run this stage again."
  log_only "cutlist: prepared — awaiting proposal"
  stage_end; exit 0
fi

# ---------------------------------------------------------------- pass 2
# A rebuilt list un-approves the old one. Colin approves what he last saw.
if stage_done "$PROJECT" "05-cutlist-approved"; then
  rm -f "$(marker_path "$PROJECT" "05-cutlist-approved")" "$DIR/locked.json"
  log_note "The cut list changed, so the earlier approval is cleared."
fi

log "Checking the proposal against the hard rules"
OUT=$("$PY" "$ROOT/scripts/cutlist.py" finalize "$DIR") \
  || die "The proposal can't be used as written — see above. Nothing was changed."
read -r NCUTS REMOVED ROUGH AFTER NADJ NDROP NOPEN <<< "$(printf '%s\n' "$OUT" | head -1)"
log_only "cutlist: proposed=$NCUTS removed=${REMOVED}s rough=${ROUGH}s after=${AFTER}s adjusted=$NADJ dropped=$NDROP"
log_cut_lines "$OUT"
log_ok "$NCUTS content cut(s) proposed — $(hms "$ROUGH") becomes $(hms "$AFTER")"
[[ "$NADJ" -gt 0 ]]  && log_note "$NADJ moved a sentence inward to land on still picture"
[[ "$NDROP" -gt 0 ]] && log_warn "$NDROP proposed cut(s) had no clean place to land — left in"
[[ "$NOPEN" -gt 0 ]] && log_warn "$NOPEN held-back name(s) would stay in the video — see cutlist.md"

mark_done "$PROJECT" "05-cutlist"
echo
printf '%s  CHECKPOINT 1%s  The cut list is waiting for Colin: work/%s/cutlist.md\n' "$YEL" "$OFF" "$PROJECT"
_write "CHECKPOINT 1 — cut list waiting for Colin: work/$PROJECT/cutlist.md"
stage_end
