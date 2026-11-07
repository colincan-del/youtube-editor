#!/usr/bin/env bash
# One chapter's graphics: build the Hyperframes project, render it as a
# transparent overlay, lay it over chapters/NN.mov. Sourced by stages 7 and 8.
# Needs common.sh loaded first.
#
#   render_chapter <project> <n> <out.mov> [limit-seconds]

HF="$ROOT/tools/hyperframes/node_modules/.bin/hyperframes"

render_chapter() {
  local project="$1" n="$2" out="$3" limit="${4:-}"
  local dir="$WORK/$project" nn gdir t0 info
  nn=$(printf '%02d' "$n"); gdir="$dir/graphics/$nn"
  [[ -x "$HF" ]] || die "Hyperframes isn't installed — run: (cd tools/hyperframes && npm install)"
  assert_not_raw "$out"

  info=$("$PY" "$ROOT/scripts/graphics.py" build "$dir" "$n" ${limit:+--limit "$limit"}) \
    || die "Chapter $n's graphics can't be built as planned — see above."
  local dur nwords nemph ncall intro
  read -r dur nwords nemph ncall intro <<< "$info"
  log_only "graphics: chapter $n duration=$dur words=$nwords emphasised=$nemph callouts=$ncall intro=$intro"

  "$HF" lint "$gdir" > "$gdir/lint.log" 2>&1 \
    || die "Hyperframes lint failed on chapter $n — see work/$project/graphics/$nn/lint.log"

  t0=$(date +%s)
  "$HF" render "$gdir" --format webm --fps 30 -o "$gdir/overlay.webm" --quiet \
    > "$gdir/render.log" 2>&1 \
    || die "Hyperframes couldn't render chapter $n — see work/$project/graphics/$nn/render.log"
  if grep -q '404 Not Found' "$gdir/render.log"; then
    die "Chapter $n rendered with missing files (fonts?) — see work/$project/graphics/$nn/render.log"
  fi
  log_only "graphics: chapter $n overlay rendered in $(( $(date +%s) - t0 ))s"

  # The overlay is 30 fps; the recording keeps its own rate. VP9 alpha needs
  # the libvpx decoder named explicitly or ffmpeg drops the transparency.
  local fps
  fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate \
        -of default=nw=1:nk=1 "$dir/chapters/$nn.mov")
  ffmpeg -hide_banner -nostats -v error -y -i "$dir/chapters/$nn.mov" \
    -c:v libvpx-vp9 -i "$gdir/overlay.webm" \
    -filter_complex "[1:v]fps=${fps}[ov];[0:v][ov]overlay=0:0:format=auto:eof_action=pass[v]" \
    -map '[v]' -map 0:a ${limit:+-t "$limit"} \
    -c:v libx264 -preset medium -crf 12 -pix_fmt yuv420p -c:a pcm_s16le -ar 48000 "$out" \
    || die "ffmpeg couldn't lay chapter $n's graphics over the video."
  RENDER_SECS=$(( $(date +%s) - t0 ))
}
