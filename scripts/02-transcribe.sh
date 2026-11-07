#!/usr/bin/env bash
# Stage 2 — Transcribe.
# WhisperX, word-level timestamps. Everything downstream — cuts, chapters,
# captions, the description — is built on the word times this produces, so this
# runs once and is cached hard.
#
# Usage:  ./scripts/02-transcribe.sh [project] [--force]
# Env:    OV_WHISPER_MODEL   default small.en   (medium.en is slower and better)

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
WAV="$DIR/audio.wav"
JSON="$DIR/transcript.json"
MODEL="${OV_WHISPER_MODEL:-small.en}"

stage_begin "2 — transcribe"
log "Project: $PROJECT"

[[ -f "$SRC" ]] || die "No work/$PROJECT/source.mp4 — run ./scripts/01-copy.sh first."
[[ -x "$PY" ]]  || die "The Python environment is missing. Expected $PY"

if skip_if_done "$PROJECT" "02-transcribe"; then
  WORDS=$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(sum(len(s.get('words',[])) for s in d['segments']))" "$JSON" 2>/dev/null || echo "?")
  log_note "$WORDS words in transcript.json"
  stage_end; exit 0
fi

# ---------------------------------------------------------------- audio
# 16 kHz mono is what the model wants. Doing the conversion here rather than
# letting WhisperX shell out means one predictable file we can reuse.
if [[ ! -f "$WAV" ]]; then
  log "Extracting audio to 16 kHz mono"
  ffmpeg -hide_banner -v error -y -i "$SRC" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$WAV" \
    || die "Couldn't extract the audio track."
fi
log_ok "audio.wav ready ($(( $(stat -f%z "$WAV" 2>/dev/null || stat -c%s "$WAV") / 1048576 )) MB)"

# ---------------------------------------------------------------- the prompt
# Whisper biases toward vocabulary it has just seen. Seeding the correct
# spellings here means fewer corrections for stage 3 to make — it does not
# replace stage 3, which still catches what slips through.
# The hint is the brand's own (brands/<name>/brand.json → transcribe_prompt).
PROMPT="$(brand_get "$(brand_dir "$PROJECT")" 'b.get("transcribe_prompt") or ""')"

log "Running WhisperX — model $MODEL, CPU"
log_note "No CUDA on Apple Silicon. Roughly 10-20 minutes for a 30-minute video."
log_only "whisperx model=$MODEL device=cpu compute_type=int8"

# WhisperX's own output goes to its own file. It emits model-download progress
# bars and torch warnings, and run.log has to stay something Colin can read.
WXLOG="$DIR/whisperx.log"

# Seen on this machine: WhisperX dies during alignment with
#   "recursive_mutex lock failed: Invalid argument"
# a native threading crash, not a problem with the recording. Capping the
# thread count avoids it, and the retry below drops to one thread. Worth the
# trouble because it lands twenty minutes into a long transcription.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

run_whisperx() {
  "$VENV/bin/whisperx" "$WAV" \
    --model "$MODEL" \
    --language en \
    --device cpu \
    --compute_type int8 \
    --batch_size 8 \
    --threads "$1" \
    --output_dir "$DIR" \
    --output_format json \
    ${PROMPT:+--initial_prompt "$PROMPT"} \
    --print_progress True \
    > "$WXLOG" 2>&1
}

# Did it write a transcript with word timings, whatever its exit status?
transcript_usable() {
  [[ -f "$DIR/audio.json" ]] || return 1
  "$PY" -c '
import json,sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
segs = d.get("segments") or []
sys.exit(0 if sum(len(s.get("words",[])) for s in segs) > 0 else 1)
' "$DIR/audio.json" 2>/dev/null
}

tidy_log() {
  # Collapse the progress bars — they are one enormous line each.
  [[ -f "$WXLOG" ]] || return 0
  tr '\r' '\n' < "$WXLOG" | grep -v -E '^\s*[0-9]+%\|' > "$WXLOG.tidy" \
    && mv "$WXLOG.tidy" "$WXLOG"
}

START=$(date +%s)
set +e
run_whisperx 4
RC=$?
set -e
tidy_log

if (( RC != 0 )) && transcript_usable; then
  log_warn "WhisperX exited with status $RC but the transcript is complete."
  log_note "It crashed on the way out, after the work was done. Continuing."
  RC=0
elif (( RC != 0 )); then
  log_warn "WhisperX failed (status $RC). Retrying on a single thread."
  log_only "whisperx: first attempt failed rc=$RC, retrying with --threads 1"
  rm -f "$DIR/audio.json"
  set +e
  run_whisperx 1
  RC=$?
  set -e
  tidy_log
  if (( RC != 0 )) && transcript_usable; then RC=0; fi
fi

if (( RC != 0 )); then
  log_err "WhisperX could not transcribe this recording."
  log_note "Why: work/$PROJECT/whisperx.log"
  log_note "A first run also downloads about 600 MB of models."
  tail -5 "$WXLOG" 2>/dev/null | while IFS= read -r l; do log_note "$l"; done
  echo; exit 1
fi
log_note "WhisperX output: work/$PROJECT/whisperx.log"

# WhisperX names its output after the input file.
[[ -f "$DIR/audio.json" ]] || die "WhisperX finished but wrote no JSON. See logs/run.log."
mv "$DIR/audio.json" "$JSON"

read -r SEGS WORDS DUR <<< "$("$PY" -c '
import json,sys
d=json.load(open(sys.argv[1]))
segs=d["segments"]
words=sum(len(s.get("words",[])) for s in segs)
end=max((s.get("end") or 0) for s in segs) if segs else 0
print(len(segs), words, round(end))
' "$JSON")"

if [[ "${WORDS:-0}" -lt 1 ]]; then
  die "The transcript came back empty. Listen to work/$PROJECT/audio.wav — there may be no speech in it."
fi

log_ok "$WORDS words across $SEGS segments, covering ${DUR}s"
log_only "transcribe: model=$MODEL words=$WORDS segments=$SEGS runtime=$(( $(date +%s) - START ))s"

# A word without timings breaks every cut decision downstream. Say so now.
NOTIME=$("$PY" -c '
import json,sys
d=json.load(open(sys.argv[1]))
print(sum(1 for s in d["segments"] for w in s.get("words",[]) if w.get("start") is None))
' "$JSON")
if [[ "$NOTIME" -gt 0 ]]; then
  log_warn "$NOTIME word(s) came back without timings — alignment was uncertain there."
  log_note "Cuts near them will be nudged to the nearest word that does have one."
fi

mark_done "$PROJECT" "02-transcribe"
stage_end
