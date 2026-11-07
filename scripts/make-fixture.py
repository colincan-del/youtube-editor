#!/usr/bin/env python3
"""Build a synthetic recording to test the pipeline without waiting on Colin.

Speech comes from macOS's `say`. The script is written to contain, deliberately:

  * "Overview" and "giant chart" — so the glossary pass has something to fix
  * a false start, said again after a pause
  * a filler word with silence either side
  * two long dead-air gaps, one over still picture and one over moving picture

The second of those is the point. The still gap should be cut; the moving gap
must not be, because Screen Studio's zooms are baked into the export and a cut
inside one jumps. If cleanup cuts both, hard rule 3 is broken.

    make-fixture.py <out-dir>
"""

import json
import subprocess
import sys
from pathlib import Path

VOICE = "Alex"

# (kind, payload) — "say" is speech, "gap" is seconds of silence.
SCRIPT = [
    ("say", "Welcome to Overview, the execution system for construction planning."),
    ("gap", 0.5),
    ("say", "Every project runs two plans."),
    ("gap", 0.8),                       # the restart
    ("say", "Every project runs two plans, the one you sold and the one you are "
            "actually building."),
    ("gap", 3.0),                       # dead air over STILL picture — cut this
    ("say", "Nobody reads the giant chart. It is out of date the day it is printed."),
    ("gap", 0.5),
    ("say", "Um."),
    ("gap", 0.5),
    ("say", "We pull the baseline out of primavera and rebuild the logic."),
    ("gap", 2.5),                       # dead air over MOVING picture — leave it
    ("say", "You never touch the software. We show up every week."),
]

MOVING_GAP_INDEX = 11   # index into SCRIPT of the gap that gets motion over it


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        raise SystemExit(f"failed: {' '.join(str(c) for c in cmd)}\n{p.stderr[-600:]}")
    return p


def duration(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
    tmp = out / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    print("Generating speech with `say`...")
    pieces = []          # (path, seconds, kind, index)
    for i, (kind, payload) in enumerate(SCRIPT):
        wav = tmp / f"{i:02d}.wav"
        if kind == "say":
            aiff = tmp / f"{i:02d}.aiff"
            run(["say", "-v", VOICE, "-o", str(aiff), payload])
            run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(aiff),
                 "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)])
        else:
            run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                 "-i", f"anullsrc=r=48000:cl=mono:d={payload}",
                 "-c:a", "pcm_s16le", str(wav)])
        pieces.append((wav, duration(wav), kind, i))

    # Where everything landed, so the video can put motion in the right place.
    timeline, t = [], 0.0
    for wav, secs, kind, i in pieces:
        timeline.append({"index": i, "kind": kind, "start": round(t, 3),
                         "end": round(t + secs, 3)})
        t += secs
    total = t
    print(f"  {total:.1f}s of audio across {len(pieces)} pieces")

    listfile = tmp / "audio.txt"
    listfile.write_text("".join(f"file '{p.resolve()}'\n" for p, _, _, _ in pieces))
    audio = tmp / "audio.wav"
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(audio)])

    moving = next(s for s in timeline if s["index"] == MOVING_GAP_INDEX)
    print(f"  motion goes over {moving['start']:.1f}s - {moving['end']:.1f}s")

    # Video: a still card everywhere, replaced by a zoom over the moving gap.
    still_a = tmp / "still_a.mp4"
    still_b = tmp / "still_b.mp4"
    move = tmp / "move.mp4"
    pre = moving["start"]
    mid = moving["end"] - moving["start"]
    post = total - moving["end"]

    # This ffmpeg build has no drawtext (no libfreetype), so the "screen" is a
    # single detailed frame held still. Detail matters: a zoom over a flat
    # colour barely changes any pixels, and the motion detector would then call
    # a zoom still — which is the exact mistake it exists to prevent.
    frame = tmp / "screen.png"
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=1280x720:rate=1:duration=1",
         "-frames:v", "1", str(frame)])

    def card(path, secs):
        run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-loop", "1",
             "-i", str(frame), "-t", f"{secs}", "-r", "30",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(path)])

    card(still_a, pre)
    card(still_b, post)
    # A slow push in — what a Screen Studio zoom looks like to a difference filter.
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-loop", "1",
         "-i", str(frame), "-t", f"{mid}", "-r", "30",
         # `zoom+0.004` does not accumulate on a looped still image — it renders
         # a motionless clip. Drive the zoom off the frame counter instead.
         "-vf", "zoompan=z='1+0.012*in':d=1:x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':s=1280x720:fps=30",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(move)])

    vlist = tmp / "video.txt"
    vlist.write_text("".join(f"file '{p.resolve()}'\n" for p in (still_a, move, still_b)))
    video = tmp / "video.mp4"
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(vlist), "-c", "copy", str(video)])

    final = out / "fixture.mp4"
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(final)])

    (out / "fixture.md").write_text(
        "# Fixture — synthetic pipeline test\n\n"
        "Not a real recording. Generated by `scripts/make-fixture.py` so stages 1-4\n"
        "can be tested without waiting on a Screen Studio export.\n\n"
        "- Welcome and what OverVue is\n"
        "- Every project runs two plans\n"
        "- Why nobody reads the Gantt chart\n"
        "- Pulling the baseline out of Primavera\n"
        "- You never touch the software\n")

    (out / "fixture.expected.json").write_text(json.dumps({
        "note": "What stages 3 and 4 are supposed to do to this file.",
        "total_seconds": round(total, 3),
        "timeline": timeline,
        "moving_gap": moving,
        "glossary_should_fix": ["Overview -> OverVue", "giant chart -> Gantt chart",
                                "primavera -> Primavera"],
        "cleanup_should": ["trim the 3.0s gap over still picture",
                           "remove the repeated 'Every project runs two plans'",
                           "leave the 2.5s gap that sits under the zoom"],
    }, indent=1))

    print(f"\n  {final}  ({duration(final):.1f}s)")
    print(f"  {out / 'fixture.md'}")
    print(f"  {out / 'fixture.expected.json'}")


if __name__ == "__main__":
    main()
