#!/usr/bin/env python3
"""Pre-cut a silent screen recording for a voiceover.

Colin recorded the first walkthrough passes with no microphone. This squeezes
one of those recordings into something he can narrate over: it changes speed
only, never cuts, so the picture never jumps (hard rule 3 has nothing to catch).

    highlight  text selected in Claude (the #B4D5FB selection blue) → 1×, padded
    busy       the picture moves: zooms, scrolls, clicks             → --busy
    quiet      nothing much moves: waiting on Claude, a still screen → at least
               --quiet, and never longer than --cap seconds a stretch

--webcam masks Screen Studio's webcam bubble (bottom left) out of both
measures, or Colin's face reads as motion. Part 1 has the bubble; 2 and 3 don't.

    precut.py <video> <out.mp4> [--busy 1] [--quiet 8] [--cap 4] [--webcam] [--dry-run]

Part 1 was exported at 2× already: --busy 2 --webcam. A pass exported at
normal speed takes --busy 4 --quiet 16 to match it.

Writes <out>.highlights.txt (where to stop and talk, in the pre-cut's own time)
and caches the measurements in <video>.precut.json, so a rerun with other
speeds skips the slow read. --dry-run prints the plan and renders nothing.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

FPS = 10                    # samples a second
W, H = 320, 180
WEBCAM = (slice(128, 180), slice(0, 75))   # rows, cols at 320×180
SELECTION = (180, 213, 251)                # Claude desktop's text selection
HL_MIN = 150                # selection-blue pixels that make a highlight
BUSY = 0.6                  # mean grey change per sample that counts as motion
QUIET_MIN = 2.0             # a shorter quiet run plays at busy speed
HL_BEFORE, HL_AFTER = 1.0, 1.5
BUSY_PAD = 0.5


def measure(video, webcam):
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-vf",
         f"fps={FPS},scale={W}:{H}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    sr, sg, sb = SELECTION
    motion, hl, prev = [], [], None
    while True:
        buf = proc.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        f = np.frombuffer(buf, np.uint8).reshape(H, W, 3).astype(np.int16)
        sel = ((abs(f[..., 0] - sr) <= 12) & (abs(f[..., 1] - sg) <= 10)
               & (abs(f[..., 2] - sb) <= 8))
        grey = f @ np.array([299, 587, 114]) / 1000.0
        if webcam:
            sel[WEBCAM] = False
            grey[WEBCAM] = 0
        motion.append(0.0 if prev is None else round(float(abs(grey - prev).mean()), 3))
        hl.append(int(sel.sum()))
        prev = grey
    if proc.wait() != 0 or not motion:
        sys.exit(f"FAIL  ffmpeg couldn't read the picture in {video}")
    return {"fps": FPS, "webcam": webcam, "motion": motion, "highlight": hl}


def dilate(mask, before, after):
    out = mask.copy()
    for i in np.flatnonzero(mask):
        out[max(0, i - before):i + after + 1] = True
    return out


def runs(labels):
    out, start = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            out.append([labels[start], start, i])
            start = i
    return out


def plan(sig, busy_speed, quiet_speed, cap):
    n = len(sig["motion"])
    hl = dilate(np.array(sig["highlight"]) >= HL_MIN, int(HL_BEFORE * FPS), int(HL_AFTER * FPS))
    busy = dilate(np.array(sig["motion"]) > BUSY, int(BUSY_PAD * FPS), int(BUSY_PAD * FPS))
    labels = ["h" if hl[i] else "b" if busy[i] else "q" for i in range(n)]
    for r in runs(labels):
        if r[0] == "q" and (r[2] - r[1]) / FPS < QUIET_MIN:
            labels[r[1]:r[2]] = ["b"] * (r[2] - r[1])
    segs = []
    for kind, a, b in runs(labels):
        dur = (b - a) / FPS
        speed = {"h": 1.0, "b": busy_speed}.get(kind) or max(quiet_speed, dur / cap)
        segs.append({"kind": kind, "start": a / FPS, "dur": dur, "speed": round(speed, 3)})
    return segs


def render_one(src, seg, path):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{seg['start']:.3f}",
         "-t", f"{seg['dur']:.3f}", "-i", str(src), "-an",
         "-vf", f"setpts=PTS/{seg['speed']}", "-r", "60",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         str(path)], check=True)
    # Each piece rounds to whole frames, so the real length drifts from the plan
    # by up to a frame a piece — 10s over 284 pieces. Highlight times use this.
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, check=True).stdout)


def notes_for(segs, lengths):
    t, notes = 0.0, []
    for s, n in zip(segs, lengths):
        if s["kind"] == "h":
            notes.append(f"{hms(t)}  ({n:.0f}s)   source {hms(s['start'])}")
        t += n
    return t, notes


def hms(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--busy", type=float, default=1.0)
    ap.add_argument("--quiet", type=float, default=8.0)
    ap.add_argument("--cap", type=float, default=4.0)
    ap.add_argument("--webcam", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cache = a.video.with_suffix(".precut.json")
    sig = None
    if cache.exists() and cache.stat().st_mtime > a.video.stat().st_mtime:
        sig = json.loads(cache.read_text())
    if not sig or sig.get("webcam", True) != a.webcam:
        print(f"Reading {a.video.name} (about a minute per 30 minutes of video)…")
        sig = measure(a.video, a.webcam)
        cache.write_text(json.dumps(sig))

    segs = plan(sig, a.busy, a.quiet, a.cap)
    t, notes = notes_for(segs, [s["dur"] / s["speed"] for s in segs])
    src_len = len(sig["motion"]) / FPS
    for k, name in (("h", "highlight"), ("b", "busy"), ("q", "quiet")):
        ss = [s for s in segs if s["kind"] == k]
        print(f"{name:9} {len(ss):4} stretches  {sum(s['dur'] for s in ss) / 60:6.1f} min in"
              f" → {sum(s['dur'] / s['speed'] for s in ss) / 60:5.1f} min out")
    print(f"total     {hms(src_len)} → {hms(t)}   ({len(notes)} highlights)")
    if a.dry_run:
        return 0

    tmp = a.out.with_suffix(".segs")
    tmp.mkdir(parents=True, exist_ok=True)
    paths = [tmp / f"{i:05d}.mp4" for i in range(len(segs))]
    with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as pool:
        lengths = list(pool.map(lambda p: render_one(a.video, *p), zip(segs, paths)))
    t, notes = notes_for(segs, lengths)
    (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in paths))
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(tmp / "list.txt"), "-c", "copy", "-movflags", "+faststart",
                    str(a.out)], check=True)
    shutil.rmtree(tmp)

    a.out.with_suffix(".highlights.txt").write_text(
        f"Highlights in {a.out.name}: stop the playback here and talk.\n"
        "Time in the pre-cut  (how long it shows)   time in the original\n\n"
        + "\n".join(notes) + "\n")
    print(f"Wrote {a.out} ({hms(t)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
