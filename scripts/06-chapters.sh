#!/usr/bin/env bash
# Stage 6 — Chapters.
# Splits the locked cut into chapters and renders each one from source.mp4.
# From here on everything happens a chapter at a time: a graphic changed in
# chapter 4 re-renders chapter 4, never the whole video.
#
#   pass 1   the locked cut as sentences in final-video time
#            → work/<project>/chapters-outline.md
#            (Claude writes chapters.proposal.json: first sentence + title)
#   pass 2   checks the titles, places the boundaries, renders
#            → work/<project>/chapters.json / .md
#            → work/<project>/chapters/NN.mov   1920×1080, levelled, no graphics
#
# Renders from source.mp4 with locked.json's keep list — never from
# cleaned.mp4, whose clock drifts a frame per splice.
#
# Usage:  ./scripts/06-chapters.sh [project] [--force]

set -o pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROJECT=""
for a in "$@"; do case "$a" in -*) ;; *) PROJECT="$a" ;; esac; done
if [[ -z "$PROJECT" ]]; then
  V="$(find_raw_video)" || exit 1
  PROJECT="$(project_of "$V")"
fi

DIR="$WORK/$PROJECT"
SRC="$DIR/source.mp4"
PROPOSAL="$DIR/chapters.proposal.json"
CHDIR="$DIR/chapters"
W=1920; H=1080
LUFS="-14"; TP="-1.5"; LRA="11"          # same targets as stage 4
LIMIT="0.75"                             # sample-peak ceiling, -2.5 dBFS: headroom for true peak

# The brand's ground colour fills the sides of a recording that isn't 16:9.
PAD="0x$(brand_get "$(brand_dir "$PROJECT")" 'b["colors"]["ground"].lstrip("#")')"

stage_begin "6 — chapters"
log "Project: $PROJECT"
need_cmd ffmpeg

stage_done "$PROJECT" "05-cutlist-approved" && [[ -f "$DIR/locked.json" ]] \
  || die "The cut isn't locked yet for '$PROJECT' — Colin answers checkpoint 1 first."
assert_not_raw "$CHDIR/x"

if stage_done "$PROJECT" "06-chapters" && (( ! FORCE )) \
   && ! [[ -f "$PROPOSAL" && "$PROPOSAL" -nt "$DIR/chapters.json" ]]; then
  log_ok "Already done — work/$PROJECT/chapters.md. Use --force to redo it."
  stage_end; exit 0
fi

# ---------------------------------------------------------------- pass 1
read -r NSENT LOCKDUR HASOUTLINE <<< "$("$PY" "$ROOT/scripts/chapters.py" prepare "$DIR")" \
  || die "Couldn't lay out the locked cut — see above."
log_ok "$NSENT sentences in the $(hms "$LOCKDUR") locked cut"
[[ "$HASOUTLINE" -eq 0 ]] && log_warn "No outline — chapters will follow the transcript alone."

if [[ ! -f "$PROPOSAL" ]]; then
  log "Waiting for the chapter proposal"
  log_note "Read work/$PROJECT/chapters-outline.md, write"
  log_note "work/$PROJECT/chapters.proposal.json, then run this stage again."
  stage_end; exit 0
fi

# ---------------------------------------------------------------- pass 2
# The chapters are cut from a constant-rate copy of the picture (chapters.py filter),
# so the grid is the average rate rounded to a whole number. A Zoom recording's
# frames land irregularly (avg 29.89 fps); cutting on that average put every edge
# off the real frames and the picture drifted 0.15s behind the sound in a chapter.
FPS=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate \
      -of default=nw=1:nk=1 "$SRC" | awk -F/ '{ printf "%d", ($2>0 ? $1/$2 : $1) + 0.5 }')
log "Placing the chapter boundaries (${FPS} fps frame grid)"
VEND=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration \
      -of default=nw=1:nk=1 "$SRC")
OUT=$("$PY" "$ROOT/scripts/chapters.py" finalize "$DIR" "$FPS" "$VEND") \
  || die "The chapter proposal can't be used as written — see above. Nothing was rendered."
read -r NCH FINALDUR NWARN <<< "$(printf '%s\n' "$OUT" | head -1)"
while IFS= read -r line; do
  case "$line" in
    CHAPTER*) log_note "${line#CHAPTER }"; log_only "chapters: ${line#CHAPTER }" ;;
    WARN*)    log_warn "${line#WARN }" ;;
  esac
done <<< "$OUT"

TMP="$DIR/.tmp-chapters"; rm -rf "$TMP"; mkdir -p "$TMP" "$CHDIR"
rm -f "$CHDIR"/*.mov

# The sound is levelled once, as one file for the whole locked cut, and each
# chapter takes its slice. Levelling chapter by chapter would let each one
# find its own gain and the joins would jump in level.
#
# Not loudnorm. On a laptop mic the clicks and plosives peak ~25 dB over the
# speech; linear loudnorm then refuses the gain and lands 3-4 LU short, and
# dynamic mode undershoots too (the test: -17.6 and -18.4). Instead: even out
# the speech (dynaudnorm), then a fixed gain into a peak limiter, the gain
# corrected by measurement until the whole cut is within half a LU of target.
log "Levelling the sound of the whole locked cut to ${LUFS} LUFS"
"$PY" "$ROOT/scripts/chapters.py" audio "$DIR" > "$TMP/audio.txt"
ffmpeg -hide_banner -nostats -v error -y -i "$SRC" -filter_complex_script "$TMP/audio.txt" \
  -map '[acat]' -c:a pcm_s24le "$TMP/joined.wav" || die "Couldn't join the locked cut's sound."
loud() {  # loud <gain> → "integrated_LUFS true_peak"
  ffmpeg -hide_banner -nostats -v info -i "$TMP/joined.wav" \
    -af "${PRE},volume=${1}dB,${POST},ebur128=peak=true" -f null - 2>&1 \
    | awk '/Integrated loudness:/{f=1} f&&/ I:/{i=$2} /True peak:/{g=1} g&&/ Peak:/{p=$2} END{print i, p}'
}
PRE="dynaudnorm=f=250:g=15:p=0.7"
POST="alimiter=limit=${LIMIT}:level=false:attack=1:release=50,aresample=48000"
read -r I0 _ <<< "$(loud 0)"
[[ -n "$I0" && "$I0" != "-inf" ]] || die "Couldn't measure the loudness of the locked cut."
GAIN=$(awk -v t="$LUFS" -v i="$I0" 'BEGIN{printf "%.2f", t-i}')
for pass in 1 2 3 4 5; do
  read -r I P <<< "$(loud "$GAIN")"
  log_only "chapters: level pass $pass gain=${GAIN}dB → ${I} LUFS, true peak ${P}"
  awk -v t="$LUFS" -v i="$I" 'BEGIN{d=t-i; exit !(d<0.5 && d>-0.5)}' && break
  # The limiter eats part of any added gain, so overshoot the correction a little.
  GAIN=$(awk -v g="$GAIN" -v t="$LUFS" -v i="$I" 'BEGIN{printf "%.2f", g+1.3*(t-i)}')
done
awk -v t="$LUFS" -v i="$I" 'BEGIN{exit !(t-i>=1.0)}' \
  && log_warn "Reached ${I} LUFS, not ${LUFS} — the recording's peaks won't allow more without distortion."
ffmpeg -hide_banner -nostats -v error -y -i "$TMP/joined.wav" \
  -af "${PRE},volume=${GAIN}dB,${POST}" -c:a pcm_s16le -ar 48000 "$TMP/levelled.wav" \
  || die "Couldn't level the sound."
log_note "${I0} LUFS → ${I} LUFS (true peak ${P} dBTP), one gain for every chapter"

# Intermediates: near-lossless picture, PCM sound. AAC between stages would
# add priming silence at every chapter join.
VF="scale=${W}:${H}:force_original_aspect_ratio=decrease:flags=lanczos,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=${PAD},setsar=1"
for (( n=1; n<=NCH; n++ )); do
  NN=$(printf '%02d' "$n")
  read -r CS CE <<< "$("$PY" -c "import json;c=json.load(open('$DIR/chapters.json'))['chapters'][$n-1];print(c['start'],c['start']+c['duration'])")"
  { "$PY" "$ROOT/scripts/chapters.py" filter "$DIR" "$n"
    printf ';\n[vcat]%s[vout];\n[acat]anullsink;\n[1:a]atrim=start=%s:end=%s,asetpts=PTS-STARTPTS[aout]\n' \
      "$VF" "$CS" "$CE"; } > "$TMP/ch$NN.txt"
  T0=$(date +%s)
  ffmpeg -hide_banner -nostats -v error -y -i "$SRC" -i "$TMP/levelled.wav" -filter_complex_script "$TMP/ch$NN.txt" \
    -map '[vout]' -map '[aout]' -c:v libx264 -preset medium -crf 12 -pix_fmt yuv420p \
    -c:a pcm_s16le -ar 48000 "$CHDIR/$NN.mov" \
    || die "ffmpeg failed rendering chapter $n. See logs/run.log."
  D=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$CHDIR/$NN.mov")
  log_ok "Chapter $n — $(hms "$D") ($(( $(date +%s) - T0 ))s to render)"
  log_only "chapters: rendered chapters/$NN.mov duration=$D"
done

rm -rf "$TMP"
log_ok "$NCH chapters, $(hms "$FINALDUR") → work/$PROJECT/chapters/"
mark_done "$PROJECT" "06-chapters"
stage_end
