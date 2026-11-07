#!/usr/bin/env python3
"""Stage 6 — split the locked cut into chapters.

From here on everything works a chapter at a time. A graphic changed in
chapter 4 re-renders chapter 4's overlay, never the whole video.

Where the chapters fall, and what they are called, is judgement — Claude's,
in session, the same as the cut list. This does the mechanics either side:

  prepare   the locked cut as numbered sentences in final-video time, with the
            outline alongside                          → chapters-outline.md
  (judge)   Claude writes chapters.proposal.json
  finalize  checks the titles against the voice rules, places each boundary
            in the pause before its first sentence, and writes one keep list
            per chapter plus its words in chapter time → chapters.json / .md
  filter    prints the ffmpeg filter for one chapter, or all the sound
            (both used by 06-chapters.sh)

The proposal file:

    {"chapters": [{"from": "S01", "title": "Every project runs two plans"},
                  {"from": "S09", "title": "..."}]}

Whatever the proposal says, these hold:
  * chapter 1 starts at 0:00 — YouTube ignores chapters otherwise
  * titles never carry a banned word or a "never" phrase (hard rules 5-7)
  * every keep edge sits on the video's frame grid, rounded inward, so picture
    and sound stay in step across hundreds of splices. Rounding inward only
    ever shrinks a keep — it cannot pull a removed word back in

    chapters.py prepare  <work-dir>
    chapters.py finalize <work-dir> <fps> [video-end]
    chapters.py filter   <work-dir> <n>
    chapters.py audio    <work-dir>
"""

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleanup import clock, load_words  # noqa: E402
from cutlist import (GENERIC, Timeline, held_back_figures, held_back_names,  # noqa: E402
                     load_json, md_cell, parse_sid, tok)

ROOT = Path(__file__).resolve().parent.parent

YT_MIN_CHAPTERS = 3      # YouTube shows no chapters below this...
YT_MIN_LENGTH = 10.0     # ...or if any chapter is shorter than this
HEAD_ROOM = 0.20         # a boundary sits at most this far ahead of the first word
TITLE_MAX = 60


def words_in(keeps, words):
    """Words whose middle lands inside a keep. Stage 4 removed fillers from
    inside sentences, so a sentence can be partly kept."""
    out, k = [], 0
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        while k < len(keeps) and keeps[k][1] <= mid:
            k += 1
        if k < len(keeps) and keeps[k][0] <= mid:
            out.append(w)
    return out


def snap(keeps, fps, video_end=None):
    """Round every edge inward to the frame grid. trim= keeps whole frames, so
    an off-grid edge loses up to a frame of picture while atrim loses none of
    the sound; over a long video that difference is audible drift."""
    out = []
    for a, b in keeps:
        if video_end is not None:
            b = min(b, video_end)       # the sound can run past the last frame
        a2 = math.ceil(a * fps - 1e-6) / fps
        b2 = math.floor(b * fps + 1e-6) / fps
        if b2 - a2 >= 1.0 / fps:
            out.append([round(a2, 6), round(b2, 6)])
    return out


def kept_sentences(work):
    locked = load_json(work / "locked.json")
    sents = load_json(work / "sentences.json")["sentences"]
    tl = Timeline(locked["keep_source"])
    out = []
    for s in sents:
        ws = [{"start": w[1], "end": w[2]} for w in s["words"]]
        n = len(words_in(locked["keep_source"], ws))
        if n * 2 > len(ws):
            s = dict(s, final_start=tl.rough(s["start"]), final_end=tl.rough(s["end"]))
            out.append(s)
    return locked, tl, out


# ------------------------------------------------------------------ prepare

def prepare(work):
    work = Path(work)
    locked, tl, sents = kept_sentences(work)
    outline = work / "outline.md"
    lines = [f"# Chapters — {locked['project']}", "",
             f"The locked cut: **{clock(tl.duration)}**, {len(sents)} sentences. "
             "Times are in the finished video.", "",
             "Write `chapters.proposal.json`: each chapter's first sentence and a title. "
             "Chapter 1 must start at the first sentence. YouTube needs at least "
             f"{YT_MIN_CHAPTERS} chapters, each at least {YT_MIN_LENGTH:.0f}s.", ""]
    if outline.exists():
        lines += ["## Outline", "", outline.read_text(encoding="utf-8").strip(), ""]
    lines += ["## Sentences", "", "| # | At | Said |", "|---|---|---|"]
    for s in sents:
        lines.append(f"| {s['id']} | {clock(s['final_start'])} | {md_cell(s['text'])} |")
    (work / "chapters-outline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(sents)} {tl.duration:.2f} {int(outline.exists())}")
    return 0


# ------------------------------------------------------------------ finalize

def voice_rules(work):
    """The brand's voice rules (stage 3's voice-rules.json), plus what its icp.md
    holds back. Hard rule 4 covers the copy Claude writes — titles, descriptions,
    cards, the thumbnail — as much as what is said on camera."""
    work = Path(work)
    rules = load_json(work / "voice-rules.json")
    bt = work / "brand.txt"
    icp = ROOT / "brands" / (bt.read_text().strip() if bt.exists() else "default") / "icp.md"
    if icp.exists():
        text = icp.read_text(encoding="utf-8")
        names = held_back_names(text)
        if names is None:
            sys.exit(f"FAIL  {icp.relative_to(ROOT)} has no readable \"cleared for use\" / "
                     "\"Held back\" split, so nothing can be checked against it. Stop and ask "
                     "Colin (hard rule 4).")
        rules["held_back"] = {"names": names, "figures": held_back_figures(text)}
    return rules


def title_problems(title, rules):
    low = title.lower()
    probs = []
    for w in rules.get("banned", []):
        if re.search(rf"\b{re.escape(w.lower())}", low):
            probs.append(f'"{w}" is banned')
    for r in rules.get("replace", []):
        if r["never"].lower() in low:
            probs.append(f'say "{r["always"]}", not "{r["never"]}"')
    # One word of a held-back name is enough, as in cutlist.find_mentions.
    held = rules.get("held_back", {})
    words = {tok(w) for w in re.split(r"[\s/—–-]+", title)}
    for name in held.get("names", []):
        if any(t in words for t in map(tok, name.split()) if len(t) > 2 and t not in GENERIC):
            probs.append(f'"{name}" is on the held-back list — no names from it in public copy')
    flat = re.sub(r"\s", "", title).replace("–", "-")
    for f in held.get("figures", []):
        if re.sub(r"\s", "", f).replace("–", "-") in flat:
            probs.append(f"{f} is on the held-back list")
    if len(title) > TITLE_MAX:
        probs.append(f"longer than {TITLE_MAX} characters")
    return probs


def finalize(work, fps, video_end=None):
    work = Path(work)
    locked, tl, sents = kept_sentences(work)
    prop = load_json(work / "chapters.proposal.json").get("chapters", [])
    rules = voice_rules(work)
    ids = [s["id"] for s in sents]
    all_ids = [s["id"] for s in load_json(work / "sentences.json")["sentences"]]

    fail = []
    if not prop:
        fail.append("The proposal has no chapters.")
    starts = []
    for i, c in enumerate(prop, 1):
        try:
            sid = all_ids[parse_sid(c.get("from"), len(all_ids))]
        except Exception:
            fail.append(f"Chapter {i}: \"{c.get('from')}\" is not a sentence.")
            continue
        if sid not in ids:
            fail.append(f"Chapter {i}: {sid} was cut — it isn't in the locked video.")
            continue
        for p in title_problems(c.get("title", ""), rules):
            fail.append(f"Chapter {i} \"{c.get('title')}\": {p}.")
        starts.append(ids.index(sid))
    if starts and starts[0] != 0:
        fail.append(f"Chapter 1 must start at {ids[0]}, the first sentence.")
    if starts != sorted(set(starts)):
        fail.append("Chapters must start on different sentences, in order.")
    if fail:
        for f in fail:
            print(f"FAIL  {f}", file=sys.stderr)
        return 1

    # Boundaries in source time. Chapter 1 starts where the locked cut starts.
    keeps = locked["keep_source"]
    bounds = [keeps[0][0]]
    for k in starts[1:]:
        first, prev = sents[k], sents[k - 1]
        gap_lo, gap_hi = prev["end"], first["start"]
        # A splice inside the gap is the cleanest place: nothing to split.
        splice = [a for a, b in keeps if gap_lo < a <= gap_hi]
        t = splice[0] if splice else max(gap_lo + (gap_hi - gap_lo) / 2, gap_hi - HEAD_ROOM)
        bounds.append(t)
    bounds.append(keeps[-1][1])

    words = load_words(work / "transcript.corrected.json")
    chapters, final_at, warn = [], 0.0, []
    for i, c in enumerate(prop):
        lo, hi = bounds[i], bounds[i + 1]
        ck = [[max(a, lo), min(b, hi)] for a, b in keeps if b > lo and a < hi]
        ck = snap(ck, fps, video_end)
        ctl = Timeline(ck)
        cw = [{"word": w["word"].strip(), "start": round(ctl.rough(w["start"]), 3),
               "end": round(ctl.rough(w["end"]), 3)} for w in words_in(ck, words)]
        s0 = starts[i]
        s1 = (starts[i + 1] if i + 1 < len(starts) else len(sents)) - 1
        chapters.append({"n": i + 1, "title": c["title"].strip(),
                         "from": sents[s0]["id"], "to": sents[s1]["id"],
                         "start": round(final_at, 3), "duration": round(ctl.duration, 3),
                         "keep_source": ck, "words": cw})
        if ctl.duration < YT_MIN_LENGTH:
            warn.append(f"Chapter {i + 1} is {ctl.duration:.1f}s — YouTube needs "
                        f"{YT_MIN_LENGTH:.0f}s or it drops every chapter marker.")
        final_at += ctl.duration
    if len(chapters) < YT_MIN_CHAPTERS:
        warn.append(f"{len(chapters)} chapters — YouTube shows chapter markers only "
                    f"from {YT_MIN_CHAPTERS}.")

    out = {"project": locked["project"], "fps": fps, "duration": round(final_at, 3),
           "note": "keep_source is in source.mp4 time, on the frame grid. words are in "
                   "chapter time — what captions and graphics key off.",
           "chapters": chapters, "warnings": warn}
    (work / "chapters.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    md = [f"# Chapters — {locked['project']}", "",
          f"**{clock(final_at)}** in {len(chapters)} chapters.", "",
          "| # | Starts | Length | Title | Sentences |", "|---|---|---|---|---|"]
    for c in chapters:
        md.append(f"| {c['n']} | {clock(c['start'])} | {c['duration']:.1f}s | "
                  f"{md_cell(c['title'])} | {c['from']}–{c['to']} |")
    if warn:
        md += ["", "## Warnings", ""] + [f"- {w}" for w in warn]
    (work / "chapters.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{len(chapters)} {final_at:.2f} {len(warn)}")
    for c in chapters:
        print(f"CHAPTER {c['n']} {clock(c['start'])} {c['duration']:.1f}s :: {c['title']}")
    for w in warn:
        print(f"WARN {w}")
    return 0


# ------------------------------------------------------------------ filter

def chapter_filter(work, n):
    """The filter_complex for one chapter: trim each keep, join them. Scaling,
    padding and loudness are added by the caller, after the join."""
    chs = load_json(Path(work) / "chapters.json")
    fps, ch = chs["fps"], chs["chapters"][n - 1]
    parts, labels = [], []
    for k, (a, b) in enumerate(ch["keep_source"]):
        # Constant-rate picture, cut by frame number: each keep is exactly as long
        # as its sound, whatever rate the recording's frames actually arrived at.
        parts.append(f"[0:v]fps={fps},trim=start_frame={round(a * fps)}:end_frame={round(b * fps)},"
                     f"setpts=PTS-STARTPTS[v{k}]")
        parts.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=1[vcat][acat]")
    print(";\n".join(parts))
    return 0


def audio_filter(work):
    """Every chapter's sound, joined — measured once for loudness so every
    chapter gets the same gain and none sounds louder than its neighbour."""
    parts, labels = [], []
    for ch in load_json(Path(work) / "chapters.json")["chapters"]:
        for a, b in ch["keep_source"]:
            k = len(labels)
            parts.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{k}]")
            labels.append(f"[a{k}]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[acat]")
    print(";\n".join(parts))
    return 0


def main():
    a = sys.argv
    if len(a) >= 3 and a[1] == "prepare":
        return prepare(a[2])
    if len(a) >= 4 and a[1] == "finalize":
        return finalize(a[2], float(a[3]), float(a[4]) if len(a) > 4 else None)
    if len(a) >= 3 and a[1] == "audio":
        return audio_filter(a[2])
    if len(a) >= 4 and a[1] == "filter":
        return chapter_filter(a[2], int(a[3]))
    print("\n".join(__doc__.strip().splitlines()[-3:]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
