#!/usr/bin/env bash
# Stage 7 — Look sample.  CHECKPOINT 2.
# Renders about ten seconds with the brand applied: the opening (intro card,
# captions) and the first chapter card and callout. Cheap to change now,
# expensive after every chapter has rendered.
#
# Needs work/<project>/graphics.plan.json — Claude's, in session: intro card,
# emphasised terms, callouts, proposed B-roll. See scripts/graphics.py.
#
#   → work/<project>/sample.mp4     what Colin watches
#   → work/<project>/sample.md      what he's being asked
#
# Usage:  ./scripts/07-sample.sh [project] [--force]
#         ./scripts/07-sample.sh [project] --approve

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/render-chapter.sh"

PROJECT=""; APPROVE=0
for a in "$@"; do case "$a" in --approve) APPROVE=1 ;; -*) ;; *) PROJECT="$a" ;; esac; done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi
DIR="$WORK/$PROJECT"
PLAN="$DIR/graphics.plan.json"

stage_begin "7 — look sample"
log "Project: $PROJECT"
stage_done "$PROJECT" "06-chapters" || die "Chapters aren't rendered yet — run ./scripts/06-chapters.sh first."

if (( APPROVE )); then
  stage_done "$PROJECT" "07-sample" || die "There is no sample to approve yet."
  mark_done "$PROJECT" "07-sample-approved"
  log_ok "Look approved by Colin — stage 8 renders every chapter this way."
  stage_end; exit 0
fi

if stage_done "$PROJECT" "07-sample" && (( ! FORCE )) && ! [[ "$PLAN" -nt "$DIR/sample.mp4" ]]; then
  log_ok "The sample is waiting on Colin: work/$PROJECT/sample.mp4"
  stage_end; exit 0
fi
[[ -f "$PLAN" ]] || {
  log "Waiting for the graphics plan"
  log_note "Write work/$PROJECT/graphics.plan.json (see scripts/graphics.py), then run this again."
  stage_end; exit 0
}
rm -f "$(marker_path "$PROJECT" "07-sample-approved")"

# Hard rule: log which brand file, colours and fonts this run used.
BDIR="$(brand_dir "$PROJECT")"
log_only "brand: $(brand_name "$PROJECT") — $(brand_get "$BDIR" '"colours " + " ".join(k+"="+v for k,v in b["colors"].items()) + " · gradient " + b["gradient"] + " · fonts " + b["fonts"]["sans"] + ", " + b["fonts"]["mono"]')"

# Which chapter shows a callout first — the sample should include one.
read -r CN CT <<< "$("$PY" - "$DIR" <<'EOF'
import json, sys
from pathlib import Path
w = Path(sys.argv[1])
plan = json.loads((w / "graphics.plan.json").read_text())
chs = json.loads((w / "chapters.json").read_text())["chapters"]
c = sorted((c for c in plan.get("callouts", []) if c.get("chapter", 0) > 1),
           key=lambda c: c["chapter"])
print(c[0]["chapter"] if c else min(2, len(chs)), "callout" if c else "card")
EOF
)"

TMP="$DIR/.tmp-sample"; rm -rf "$TMP"; mkdir -p "$TMP"
log "Rendering the opening of chapter 1"
render_chapter "$PROJECT" 1 "$TMP/a.mov" 5
log_ok "Opening — ${RENDER_SECS}s to render"
if [[ "$CN" -gt 1 ]]; then
  WHAT="its chapter card"; [[ "$CT" == "callout" ]] && WHAT="its chapter card and callout"
  log "Rendering the start of chapter $CN ($WHAT)"
  render_chapter "$PROJECT" "$CN" "$TMP/b.mov" 8
  log_ok "Chapter $CN — ${RENDER_SECS}s to render"
  printf "file '%s'\nfile '%s'\n" "$TMP/a.mov" "$TMP/b.mov" > "$TMP/list.txt"
else
  printf "file '%s'\n" "$TMP/a.mov" > "$TMP/list.txt"
fi
ffmpeg -hide_banner -v error -y -f concat -safe 0 -i "$TMP/list.txt" \
  -c:v libx264 -crf 16 -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart "$DIR/sample.mp4" \
  || die "Couldn't join the sample."
rm -rf "$TMP"
# The sample's overlays are shortened chapters; stage 8 rebuilds them in full.
rm -rf "$DIR/graphics"/*/overlay.webm

"$PY" - "$DIR" <<'EOF'
import json, sys
from pathlib import Path
w = Path(sys.argv[1])
plan = json.loads((w / "graphics.plan.json").read_text())
chs = json.loads((w / "chapters.json").read_text())["chapters"]
L = [f"# Look sample — {w.name}", "",
     "**Checkpoint 2.** Watch `sample.mp4` (about 13 seconds). This is how every chapter will look.", "",
     "**To answer:** \"approve\", or say what to change — colours, the cards.", "",
     "## What's in it", "",
     "- **Intro title card** — the brand gradient, title and a small label above it",
     "- **No burned-in captions** — YouTube gets `captions.srt`, which viewers can turn on and off",
     "- **Chapter card** — lower left, for 3 seconds at the start of each chapter",
     "- **Callout** — top right, for a number worth holding on screen", "",
     "## Chapters", ""]
for c in chs:
    L.append(f"{c['n']}. {c['title']} ({c['duration']:.0f}s)")
L += ["", "## B-roll", ""]
br = plan.get("broll", [])
if br:
    L.append(f"{len(br)} shot(s) proposed — nothing generated until you say yes:")
    L.append("")
    for b in br:
        L.append(f"- Chapter {b['chapter']} at {b['at']}, {b['seconds']}s — {b['prompt']} *({b.get('why','')})*")
else:
    L.append("None proposed. B-roll goes only where the "
              "screen isn't the content. Generating it needs a Higgsfield account "
              "(about $2–7 a video by the reference workflow).")
(w / "sample.md").write_text("\n".join(L) + "\n")
EOF

mark_done "$PROJECT" "07-sample"
D=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$DIR/sample.mp4")
log_ok "sample.mp4 — $(hms "$D")"
echo
printf '%s  CHECKPOINT 2%s  The look sample is waiting for Colin: work/%s/sample.mp4\n' "$YEL" "$OFF" "$PROJECT"
_write "CHECKPOINT 2 — look sample waiting for Colin: work/$PROJECT/sample.mp4"
stage_end
