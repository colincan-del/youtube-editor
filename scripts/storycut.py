#!/usr/bin/env python3
"""Build a story cut from silent screen recordings, ready for a voiceover.

Takes the moments listed in a JSON spec, in the spec's order, from the source
recordings (not the pre-cut), and joins them into one silent video for Colin
to narrate over. Each moment can:

    speed   play faster or slower than the file (part 1 was exported at 2×,
            so its moments take 0.5 to play at real speed)
    zoom    zoom 1.5× into Claude's panel between two source times. The camera
            follows whatever text is selected (the #B4D5FB selection blue), so
            it lands where Colin highlighted; "y" sets where it starts
    ff      squeeze the moment into this many seconds, with a fast-forward badge

    storycut.py <spec.json> <out.mp4> [--only N,N…]

Spec: {"patch": {"part1.mp4": [x, y, w, h]},           (cover the webcam bubble)
       "segments": [{"chapter": "…", "note": "…", "src": "part3.mp4",
                     "from": "0:43:51", "to": "0:44:33", "speed": 1,
                     "zoom": ["0:43:52", "0:44:30"], "y": 540, "ff": 6}, …]}
Source paths are relative to the spec. Writes <out>.guide.txt: each moment's
time in the story cut and its note, to narrate from. Hard cuts between moments;
--only renders just those segments (1-based), for a quick look.
"""

import argparse
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FW, FH, FPS = 1920, 1080, 60
ZOOM = 1.5
EASE = 0.6                  # seconds to zoom in or out
FOLLOW = 0.08               # how quickly the camera catches up with the selection, per frame
PANEL = 720                 # Claude's panel is the left 720 px
SELECTION = (180, 213, 251)
MARGIN = 40                 # space kept above and below the selected text
ROOT = Path(__file__).resolve().parent.parent
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def sec(t):
    if isinstance(t, (int, float)):
        return float(t)
    h, m, s = (["0", "0"] + t.split(":"))[-3:]
    return int(h) * 3600 + int(m) * 60 + float(s)


def hms(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def badge(path):
    """The fast-forward badge, in the active brand's colours and font."""
    name = (ROOT / "brands/active").read_text().strip()
    brand = json.loads((ROOT / f"brands/{name}/brand.json").read_text())
    font = ImageFont.truetype(str(ROOT / f"brands/{name}/fonts/{brand['fonts']['sans']}-600.woff2"), 34)
    text = "Fast-forward"
    tw = font.getbbox(text)[2]
    img = Image.new("RGBA", (tw + 120, 70), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, 69], 35, fill=brand["colors"]["ground"])
    for x in (28, 50):
        d.polygon([(x, 22), (x, 48), (x + 20, 35)], fill=brand["colors"]["accent"])
    d.text((88, 33), text, font=font, fill="white", anchor="lm")
    img.save(path)


def selected_rows(frame):
    """Top and bottom of the selected text in Claude's panel, or None."""
    f = frame[::4, :PANEL:4].astype(np.int16)
    sr, sg, sb = SELECTION
    hit = (abs(f[..., 0] - sr) <= 12) & (abs(f[..., 1] - sg) <= 10) & (abs(f[..., 2] - sb) <= 8)
    rows = np.flatnonzero(hit.sum(axis=1) >= 5)
    return (rows[0] * 4, rows[-1] * 4 + 4) if len(rows) else None


def render_one(base, patch, seg, path, badge_png):
    src = base / seg["src"]
    start, end = sec(seg["from"]), sec(seg["to"])
    speed = (end - start) / seg["ff"] if seg.get("ff") else seg.get("speed", 1.0)
    vf = []
    if seg["src"] in patch:
        x, y, w, h = patch[seg["src"]]
        vf.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0xFDFDFD:t=fill")
    vf += [f"setpts=PTS/{speed}", f"fps={FPS}"]
    cut = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", str(src), "-an"]

    if seg.get("ff"):
        subprocess.run(cut + ["-i", str(badge_png), "-filter_complex",
                              f"[0]{','.join(vf)}[v];[v][1]overlay=W-w-48:H-h-48"]
                       + ENCODE + [str(path)], check=True)
    elif not seg.get("zoom"):
        subprocess.run(cut + ["-vf", ",".join(vf)] + ENCODE + [str(path)], check=True)
    else:
        za, zb = ((sec(z) - start) / speed for z in seg["zoom"])
        dec = subprocess.Popen(cut + ["-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               stdout=subprocess.PIPE)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-s", f"{FW}x{FH}", "-r", str(FPS), "-i", "-"]
                               + ENCODE + [str(path)], stdin=subprocess.PIPE)
        cy, i = None, 0
        while len(buf := dec.stdout.read(FW * FH * 3)) == FW * FH * 3:
            t = i / FPS
            z = 1 + (ZOOM - 1) * smooth((t - za) / EASE) * (1 - smooth((t - zb + EASE) / EASE))
            frame = np.frombuffer(buf, np.uint8).reshape(FH, FW, 3)
            vh = FH / ZOOM
            sel = selected_rows(frame)
            if sel:
                top, bot = sel[0] - MARGIN, sel[1] + MARGIN
                target = (top + bot) / 2 if bot - top <= vh else bot - vh / 2
            else:
                target = cy if cy is not None else seg.get("y", FH / 2)
            cy = target if cy is None else cy + (target - cy) * FOLLOW
            if z > 1.001:
                w, h = FW / z, FH / z
                y0 = min(max(cy - h / 2, 0), FH - h)
                buf = Image.frombuffer("RGB", (FW, FH), buf).resize(
                    (FW, FH), Image.BICUBIC, box=(0, y0, w, y0 + h)).tobytes()
            enc.stdin.write(buf)
            i += 1
        enc.stdin.close()
        if dec.wait() != 0 or enc.wait() != 0:
            raise SystemExit(f"FAIL  ffmpeg stopped on {seg['src']} {seg['from']}")
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, check=True).stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--only", help="comma-separated segment numbers, 1-based")
    a = ap.parse_args()

    spec = json.loads(a.spec.read_text())
    segs = spec["segments"]
    if a.only:
        segs = [segs[int(n) - 1] for n in a.only.split(",")]
    tmp = a.out.with_suffix(".segs")
    tmp.mkdir(parents=True, exist_ok=True)
    badge(tmp / "badge.png")
    paths = [tmp / f"{i:03d}.mp4" for i in range(len(segs))]
    with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as pool:
        lengths = list(pool.map(
            lambda p: render_one(a.spec.parent, spec.get("patch", {}), *p, tmp / "badge.png"),
            zip(segs, paths)))
    (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in paths))
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(tmp / "list.txt"), "-c", "copy", "-movflags", "+faststart",
                    str(a.out)], check=True)
    shutil.rmtree(tmp)

    t, lines, chapter = 0.0, [f"Story cut {a.out.name}: what's on screen when, to narrate from.", ""], None
    for s, n in zip(segs, lengths):
        if s.get("chapter") and s["chapter"] != chapter:
            chapter = s["chapter"]
            lines += ["", chapter]
        what = "fast-forward" if s.get("ff") else s.get("note", "")
        lines.append(f"  {hms(t)}  ({n:.0f}s)  {what}   [{s['src']} {s['from']}]")
        t += n
    a.out.with_suffix(".guide.txt").write_text("\n".join(lines) + "\n")
    print(f"Wrote {a.out} ({hms(t)}) and {a.out.with_suffix('.guide.txt').name}")


if __name__ == "__main__":
    main()
