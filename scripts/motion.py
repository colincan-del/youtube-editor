#!/usr/bin/env python3
"""Picture-motion timeline for one video.

Screen Studio bakes its zooms and cursor smoothing into the export, so the
system cannot know where the camera moves except by looking at the pixels. A
cut that lands inside a zoom jumps — hard rule 3 — so every stage that splices
consults this.

Decodes at 10 fps and 160px wide in greyscale, takes the frame-to-frame
difference, and records its mean. Cheap enough to run on a 30-minute file.

    motion.py <video> <out.json>
"""

import json
import re
import subprocess
import sys
from pathlib import Path

FPS = 10
WIDTH = 160

LINE = re.compile(r"pts_time:([0-9.]+)")
YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def measure(video):
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(video),
        "-vf", (f"fps={FPS},scale={WIDTH}:-2,format=gray,"
                "tblend=all_mode=difference,signalstats,"
                "metadata=print:key=lavfi.signalstats.YAVG:file=-"),
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg failed reading the picture:\n{proc.stderr[-800:]}")

    times, scores = [], []
    pending = None
    for line in proc.stdout.splitlines():
        m = LINE.search(line)
        if m:
            pending = float(m.group(1))
            continue
        m = YAVG.search(line)
        if m and pending is not None:
            times.append(round(pending, 2))
            scores.append(round(float(m.group(1)), 4))
            pending = None
    return times, scores


def main():
    if len(sys.argv) < 3:
        print("usage: motion.py <video> <out.json>", file=sys.stderr)
        return 2
    times, scores = measure(sys.argv[1])
    if not scores:
        print("FAIL  Read no frames from the video.", file=sys.stderr)
        return 1

    ordered = sorted(scores)
    pct = lambda q: ordered[min(len(ordered) - 1, int(len(ordered) * q / 100))]
    median = pct(50)
    # A still screen recording sits near zero. The threshold floats off the
    # median so a busy video does not read as motion everywhere, but it never
    # drops below an absolute floor or a completely static screen with
    # compression noise would look like it is moving.
    still_threshold = max(0.6, median * 1.8)
    still = sum(1 for s in scores if s <= still_threshold)

    Path(sys.argv[2]).write_text(json.dumps({
        "fps": FPS,
        "times": times,
        "scores": scores,
        "median": round(median, 4),
        "still_threshold": round(still_threshold, 4),
        # Kept for diagnosis. If a real recording comes back with almost no
        # still picture, compare p25 against the 0.6 floor — a noisier encode
        # sits higher than this fixture does, and the floor is what to raise.
        "percentiles": {q: round(pct(q), 4) for q in (10, 25, 50, 75, 90, 99)},
    }), encoding="utf-8")

    print(f"{len(scores)} {round(median,3)} {round(still_threshold,3)} "
          f"{round(100.0*still/len(scores))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
