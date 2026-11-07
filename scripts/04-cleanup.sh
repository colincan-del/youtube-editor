#!/usr/bin/env bash
# Stage 4 — Auto-cleanup.
# Dead air, filler words and false starts out; audio levelled. Runs unattended —
# this is the stage Colin does not review, so it only makes cuts it can prove
# are safe. Anything it isn't sure of is left in and written down.
#
# Usage:  ./scripts/04-cleanup.sh [project] [--force]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROJECT="${1:-}"
[[ "$PROJECT" == --* || "$PROJECT" == -f ]] && PROJECT=""
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi

DIR="$WORK/$PROJECT"
SRC="$DIR/source.mp4"
TRANSCRIPT="$DIR/transcript.corrected.json"
OUTMP4="$DIR/cleaned.mp4"
TMP="$DIR/tmp-cleanup"

# YouTube normalises to about -14 LUFS. Landing there means it applies no gain
# of its own, so what Colin approves is what viewers hear.
LUFS="-14"; TP="-1.5"; LRA="11"

stage_begin "4 — cleanup"
log "Project: $PROJECT"

[[ -f "$SRC" ]] || die "No work/$PROJECT/source.mp4 — run ./scripts/01-copy.sh first."
[[ -f "$TRANSCRIPT" ]] || die "No corrected transcript — run ./scripts/03-glossary.sh first."
need_cmd ffmpeg
assert_not_raw "$OUTMP4"

if skip_if_done "$PROJECT" "04-cleanup"; then
  log_note "work/$PROJECT/cleaned.mp4"
  stage_end; exit 0
fi

DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$SRC")
log "Source is $(hms "$DUR")"

# ---------------------------------------------------------------- silence
# Read from the audio, not from gaps between words. WhisperX pads its word
# boundaries and a gap in the transcript is not proof the room was quiet.
if [[ ! -s "$DIR/silence.txt" || $FORCE -eq 1 ]]; then
  log "Finding the silences"
  ffmpeg -hide_banner -nostats -v info -i "$SRC" \
    -af "silencedetect=noise=-35dB:d=0.35" -f null - 2>&1 \
    | grep -E 'silence_(start|end)' > "$DIR/silence.txt"
fi
SILENCES=$(grep -c silence_start "$DIR/silence.txt" 2>/dev/null || echo 0)
log_ok "$SILENCES silences of 0.35s or more"

# ---------------------------------------------------------------- motion
# Hard rule 3. Screen Studio's zooms are baked into the picture, so the only
# way to know where they are is to look.
if [[ ! -s "$DIR/motion.json" || $FORCE -eq 1 ]]; then
  log "Reading the picture for motion — this is where the zooms are"
  MOUT=$("$PY" "$ROOT/scripts/motion.py" "$SRC" "$DIR/motion.json") \
    || die "Couldn't analyse the picture. See logs/run.log."
  read -r FRAMES MEDIAN THRESH STILLPC <<< "$MOUT"
  log_ok "$FRAMES samples — ${STILLPC}% of the video is still picture"
  log_only "motion: median=$MEDIAN threshold=$THRESH still=${STILLPC}%"
  if [[ "${STILLPC:-100}" -lt 25 ]]; then
    log_warn "Only ${STILLPC}% of this recording holds still."
    log_note "Expect fewer cuts than usual — most of them would jump."
  fi
fi

# ---------------------------------------------------------------- decide
log "Deciding what comes out"
RESULT=$("$PY" "$ROOT/scripts/cleanup.py" "$TRANSCRIPT" "$DIR/silence.txt" \
         "$DIR/motion.json" "$DUR" "$DIR") || {
  log_err "Cleanup analysis refused to continue — see the reason above."
  echo; exit 1
}
read -r NCUTS NKEEPS REMOVED DUR_IN DUR_OUT NSKIP NQUIET <<< "$RESULT"

log_ok "$NCUTS cuts, ${REMOVED}s removed — ${DUR_IN}s becomes ${DUR_OUT}s"
[[ "${NSKIP:-0}" -gt 0 ]] && log_note "$NSKIP safe-looking cuts left in because the picture was moving"
[[ "${NQUIET:-0}" -gt 0 ]] && log_note "$NQUIET 'silence(s)' had quiet speech in them — trimmed only around the words"
log_note "Detail: work/$PROJECT/cleanup-notes.md"
log_only "cleanup: cuts=$NCUTS keeps=$NKEEPS removed=${REMOVED}s quiet_speech=${NQUIET:-0}"

if [[ "$NCUTS" -eq 0 ]]; then
  log "Nothing to cut. Levelling the audio only."
fi

# ---------------------------------------------------------------- render
rm -rf "$TMP"; mkdir -p "$TMP"

# Rendered in batches. One filter_complex holding 200 trim pairs for a
# 30-minute recording is where this falls over on the real files, and it would
# fall over at the end of a long decode.
BATCH=40
"$PY" - "$DIR/cleanup.json" "$TMP" "$BATCH" <<'PYEOF'
import json, sys
keeps = json.load(open(sys.argv[1]))["keep"]
tmp, batch = sys.argv[2], int(sys.argv[3])
for n, i in enumerate(range(0, len(keeps), batch)):
    group = keeps[i:i + batch]
    parts, labels = [], []
    for k, (a, b) in enumerate(group):
        parts.append(f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{k}]")
        parts.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    parts.append("".join(labels) + f"concat=n={len(group)}:v=1:a=1[vout][aout]")
    open(f"{tmp}/batch{n:03d}.txt", "w").write(";".join(parts))
PYEOF

NBATCH=$(ls "$TMP"/batch*.txt 2>/dev/null | wc -l | tr -d ' ')
[[ "$NBATCH" -gt 0 ]] || die "No footage left to render. Check work/$PROJECT/cleanup.json."
log "Rendering $NBATCH batch(es)"

i=0
for f in "$TMP"/batch*.txt; do
  i=$((i+1))
  printf '    batch %d of %d\r' "$i" "$NBATCH"
  ffmpeg -hide_banner -nostats -v error -y -i "$SRC" \
    -filter_complex_script "$f" -map '[vout]' -map '[aout]' \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -ar 48000 \
    "$TMP/part$(printf '%03d' "$i").mp4" \
    || die "ffmpeg failed on batch $i. See logs/run.log."
done
printf '                              \r'
log_ok "$NBATCH batch(es) rendered"

if [[ "$NBATCH" -eq 1 ]]; then
  mv "$TMP/part001.mp4" "$TMP/joined.mp4"
else
  : > "$TMP/list.txt"
  for p in "$TMP"/part*.mp4; do printf "file '%s'\n" "$p" >> "$TMP/list.txt"; done
  ffmpeg -hide_banner -v error -y -f concat -safe 0 -i "$TMP/list.txt" \
    -c copy "$TMP/joined.mp4" || die "Joining the batches failed."
  log_ok "Batches joined"
fi

# ---------------------------------------------------------------- audio level
# Two passes. One-pass loudnorm rides the gain and audibly pumps on speech.
log "Levelling the audio to ${LUFS} LUFS"
MEASURE=$(ffmpeg -hide_banner -nostats -v info -i "$TMP/joined.mp4" \
  -af "loudnorm=I=${LUFS}:TP=${TP}:LRA=${LRA}:print_format=json" -f null - 2>&1 \
  | sed -n '/^{/,/^}/p')

read -r M_I M_TP M_LRA M_THRESH <<< "$("$PY" -c '
import json,sys
try:
    d=json.loads(sys.stdin.read())
    print(d["input_i"], d["input_tp"], d["input_lra"], d["input_thresh"])
except Exception:
    print("none none none none")
' <<<"$MEASURE")"

if [[ "$M_I" == "none" || "$M_I" == "-inf" ]]; then
  log_warn "Couldn't measure the loudness — levelling in a single pass instead."
  AF="loudnorm=I=${LUFS}:TP=${TP}:LRA=${LRA}"
else
  log_note "was ${M_I} LUFS, peak ${M_TP} dBTP"
  AF="loudnorm=I=${LUFS}:TP=${TP}:LRA=${LRA}:measured_I=${M_I}:measured_TP=${M_TP}:measured_LRA=${M_LRA}:measured_thresh=${M_THRESH}:linear=true"
fi
log_only "loudnorm: target=${LUFS} LUFS measured_I=${M_I}"

ffmpeg -hide_banner -v error -y -i "$TMP/joined.mp4" \
  -c:v copy -af "$AF" -c:a aac -b:a 192k -ar 48000 \
  "$OUTMP4" || die "Levelling the audio failed."

FINAL_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUTMP4")
log_ok "cleaned.mp4 — $(hms "$FINAL_DUR")"
log_only "cleanup: in=$(hms "$DUR") out=$(hms "$FINAL_DUR")"

rm -rf "$TMP"
mark_done "$PROJECT" "04-cleanup"
stage_end
