#!/usr/bin/env python3
"""Stage 10 — the package: title, description, captions, thumbnail, LinkedIn clip.

The words are judgement — Claude's, in session. This does the mechanics:

  prepare   the finished video as sentences in final time, with the chapters
                                                        → package-brief.md
  (judge)   Claude writes package.proposal.json
  finalize  checks every line against the brand's voice rules, then writes
            package/title.txt, description.txt (chapters appended),
            captions.srt, and the thumbnail and LinkedIn Hyperframes projects

The proposal file:

    {"title": "...",
     "description": "Two or three short paragraphs. Chapters are added for you.",
     "thumbnail": {"eyebrow": "...", "headline": "3 to 6 words",
                   "accent_word": "one word of the headline", "frame_at": "S12"},
     "linkedin":  {"from": "S16", "to": "S19", "eyebrow": "...", "hook": "one line"}}

frame_at is a sentence id: the thumbnail's picture is taken as it begins.

    package.py prepare  <work-dir>
    package.py finalize <work-dir>
"""

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chapters import kept_sentences, title_problems, voice_rules  # noqa: E402
from cleanup import clock  # noqa: E402
from cutlist import Timeline, load_json, md_cell  # noqa: E402
from graphics import mark_emphasis  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "graphics"
SRT_CHARS = 42          # one line of a YouTube caption, comfortably
SRT_WORDS = 8
SRT_PAUSE = 0.5
SRT_MIN = 0.8
LINKEDIN_MAX = 90.0
LINKEDIN_PAD = (0.3, 0.5)   # breath kept before the first word and after the last


def yt_time(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def final_words(chs):
    """Every kept word, in the finished video's time."""
    out = []
    for c in chs["chapters"]:
        for w in c["words"]:
            out.append({"text": w["word"], "start": round(c["start"] + w["start"], 3),
                        "end": round(c["start"] + w["end"], 3)})
    return out


def srt(words):
    cues, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = " ".join(x["text"] for x in cur)
        if (not nxt or len(cur) >= SRT_WORDS or re.search(r"[.?!]$", w["text"])
                or nxt["start"] - w["end"] >= SRT_PAUSE
                or len(text) + 1 + len(nxt["text"]) > SRT_CHARS):
            cues.append(cur)
            cur = []
    lines = []
    for i, c in enumerate(cues):
        start, end = c[0]["start"], c[-1]["end"] + 0.2
        end = max(end, start + SRT_MIN)
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0]["start"] - 0.01)
        lines += [str(i + 1), f"{srt_time(start)} --> {srt_time(end)}",
                  " ".join(x["text"] for x in c), ""]
    return "\n".join(lines), len(cues)


def to_final(chs, t):
    """A source-time moment → finished-video time, through the chapters'
    frame-snapped keeps (the locked cut's own clock is off by the snapping)."""
    best = None
    for c in chs["chapters"]:
        for a, b in c["keep_source"]:
            d = 0.0 if a <= t <= b else min(abs(t - a), abs(t - b))
            if best is None or d < best[0]:
                best = (d, c["start"] + Timeline(c["keep_source"]).rough(min(max(t, a), b)))
    return best[1]


def brand_of(work):
    name = (work / "brand.txt").read_text().strip() if (work / "brand.txt").exists() else "default"
    return ROOT / "brands" / name


# ------------------------------------------------------------------ prepare

def prepare(work):
    work = Path(work)
    chs = load_json(work / "chapters.json")
    _, _, sents = kept_sentences(work)
    L = [f"# Package brief — {chs['project']}", "",
         f"Finished video: **{clock(chs['duration'])}**, {len(chs['chapters'])} chapters.", "",
         "Write `package.proposal.json` — format in `scripts/package.py`. Every line is checked "
         "against the brand's voice rules. Read the brand's `voice.md` / `icp.md` if it has them.", "",
         "## Chapters", ""]
    L += [f"- {yt_time(c['start'])} {c['title']}" for c in chs["chapters"]]
    outline = work / "outline.md"
    if outline.exists():
        L += ["", "## Outline", "", outline.read_text(encoding="utf-8").strip()]
    L += ["", "## Sentences", "", "| # | At | Said |", "|---|---|---|"]
    L += [f"| {s['id']} | {clock(s['final_start'])} | {md_cell(s['text'])} |" for s in sents]
    (work / "package-brief.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{len(sents)} {chs['duration']}")
    return 0


# ------------------------------------------------------------------ finalize

def finalize(work):
    work = Path(work)
    chs = load_json(work / "chapters.json")
    prop = load_json(work / "package.proposal.json")
    rules = voice_rules(work)
    plan_p = work / "graphics.plan.json"
    plan = load_json(plan_p) if plan_p.exists() else {}
    bdir = brand_of(work)
    brand = load_json(bdir / "brand.json")
    _, _, sents = kept_sentences(work)
    by_id = {s["id"]: s for s in sents}

    th, li = prop.get("thumbnail", {}), prop.get("linkedin", {})
    fail, warn = [], []
    checks = [("Title", prop.get("title", "")), ("Thumbnail eyebrow", th.get("eyebrow", "")),
              ("Thumbnail headline", th.get("headline", "")),
              ("LinkedIn eyebrow", li.get("eyebrow", "")), ("LinkedIn hook", li.get("hook", ""))]
    for what, text in checks:
        for p in title_problems(text, rules):
            if "longer than" not in p:
                fail.append(f'{what} "{text}": {p}.')
    for p in title_problems(prop.get("description", ""), rules):
        if "longer than" not in p:
            fail.append(f"Description: {p}.")
    if not prop.get("title"):
        fail.append("No title.")
    elif len(prop["title"]) > 100:
        fail.append(f"Title is {len(prop['title'])} characters — YouTube allows 100.")
    n = len(th.get("headline", "").split())
    if not 3 <= n <= 6:
        warn.append(f"Thumbnail headline is {n} words — 3 to 6 reads at thumbnail size.")
    for key, what in ((th.get("frame_at"), "Thumbnail frame_at"),
                      (li.get("from"), "LinkedIn from"), (li.get("to"), "LinkedIn to")):
        if key not in by_id:
            fail.append(f"{what}: {key} isn't a sentence in the finished video.")
    if fail:
        for f in fail:
            print(f"FAIL  {f}", file=sys.stderr)
        return 1

    out = work / "package"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    # title, description with chapters, captions
    (out / "title.txt").write_text(prop["title"].strip() + "\n", encoding="utf-8")
    chapters = "\n".join(f"{yt_time(c['start'])} {c['title']}" for c in chs["chapters"])
    desc = prop["description"].strip() + "\n\nChapters\n" + chapters + "\n"
    if len(desc) > 5000:
        print(f"FAIL  Description is {len(desc)} characters — YouTube allows 5000.", file=sys.stderr)
        return 1
    (out / "description.txt").write_text(desc, encoding="utf-8")
    words = final_words(chs)
    text, ncues = srt(words)
    (out / "captions.srt").write_text(text, encoding="utf-8")

    common = {"brand": {k: brand.get(k) for k in ("colors", "gradient", "fonts")}}

    # thumbnail project — the shell script drops frame.jpg in before the snapshot
    tdir = out / "thumbnail"
    shutil.copytree(TEMPLATE / "lib", tdir / "lib")
    shutil.copytree(bdir / "fonts", tdir / "lib" / "fonts")
    tdata = dict(common, eyebrow=th.get("eyebrow", ""), headline=th["headline"],
                 accent_word=th.get("accent_word", ""))
    html = (TEMPLATE / "thumbnail.html").read_text(encoding="utf-8")
    (tdir / "index.html").write_text(html.replace("/*__DATA__*/ null", json.dumps(tdata)), encoding="utf-8")
    frame_at = to_final(chs, by_id[th["frame_at"]]["start"]) + 0.5

    # LinkedIn clip project
    a = max(0.0, to_final(chs, by_id[li["from"]]["start"]) - LINKEDIN_PAD[0])
    b = min(chs["duration"], to_final(chs, by_id[li["to"]]["end"]) + LINKEDIN_PAD[1])
    # The pad must not reach into the neighbouring sentences' words.
    ws = final_words(chs)
    before = [w["end"] for w in ws if w["end"] <= a + LINKEDIN_PAD[0] - 0.01]
    after = [w["start"] for w in ws if w["start"] >= b - LINKEDIN_PAD[1] + 0.01]
    if before:
        a = max(a, (before[-1] + a + LINKEDIN_PAD[0]) / 2)
    if after:
        b = min(b, (after[0] + b - LINKEDIN_PAD[1]) / 2)
    if b - a > LINKEDIN_MAX:
        warn.append(f"LinkedIn clip is {b - a:.0f}s — over {LINKEDIN_MAX:.0f}s loses people.")
    lw = [{"text": w["text"], "start": round(w["start"] - a, 3), "end": round(w["end"] - a, 3)}
          for w in words if w["start"] >= a and w["end"] <= b]
    mark_emphasis(lw, plan.get("emphasis", []))
    ldir = out / "linkedin"
    shutil.copytree(TEMPLATE / "lib", ldir / "lib")
    shutil.copytree(bdir / "fonts", ldir / "lib" / "fonts")
    ldata = dict(common, duration=round(b - a, 3), eyebrow=li.get("eyebrow", ""),
                 hook=li.get("hook", ""), words=lw)
    html = (TEMPLATE / "linkedin.html").read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/ null", json.dumps(ldata, ensure_ascii=False))
    html = html.replace('data-start="0" data-duration="10"', f'data-start="0" data-duration="{ldata["duration"]}"')
    (ldir / "index.html").write_text(html, encoding="utf-8")

    print(f"{ncues} {frame_at:.3f} {a:.3f} {b:.3f} {len(warn)}")
    for w in warn:
        print(f"WARN {w}")
    return 0


def main():
    a = sys.argv
    if len(a) == 3 and a[1] == "prepare":
        return prepare(a[2])
    if len(a) == 3 and a[1] == "finalize":
        return finalize(a[2])
    print("\n".join(__doc__.strip().splitlines()[-2:]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
