#!/usr/bin/env python3
"""Stages 7 and 8 — build one chapter's graphics as a Hyperframes project.

Each chapter gets its own folder, work/<project>/graphics/NN/, holding a copy
of templates/graphics/chapter.html with that chapter's data written in. Hyperframes
renders it as a transparent overlay; ffmpeg lays it over chapters/NN.mov. The
video never passes through Chrome, and a changed graphic re-renders one chapter.

What goes on screen comes from two places:
  chapters.json         the words (captions) and chapter titles — mechanical
  graphics.plan.json    Claude's judgement, in session: the intro card, which
                        terms are emphasised, which numbers get a callout

The plan file:

    {"intro":    {"eyebrow": "Brand name", "title": "The video's title",
                  "sub": "optional line under it"},
     "emphasis": ["product name", "key term", "30 minutes"],
     "callouts": [{"chapter": 3, "at": "S18", "label": "GitHub stars",
                   "value": "85,000", "duration": 4.0}],
     "broll":    [{"chapter": 1, "at": "S01", "seconds": 4,
                   "prompt": "...", "why": "..."}]}

Numbers are emphasised without being listed. Callout "at" is a sentence id —
the callout lands as that sentence begins. B-roll is proposed here and priced
at checkpoint 2; nothing is generated until Colin approves.

Every piece of text that goes on screen, other than Colin's own words in the
captions, is checked against the voice rules (hard rules 5-7).

    graphics.py build <work-dir> <n> [--limit seconds]
"""

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chapters import title_problems, voice_rules  # noqa: E402
from cutlist import Timeline, load_json, parse_sid  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "graphics"
CALLOUT_DEFAULT = 4.0
NUMBER = re.compile(r"\d")


def tokens(s):
    return [t for t in re.sub(r"[^a-z0-9$%']+", " ", s.lower()).split() if t]


def mark_emphasis(words, terms):
    """Flag words that belong to an emphasised term, or carry a number."""
    seqs = [tokens(t) for t in terms if tokens(t)]
    toks = [tokens(w["text"]) for w in words]
    flat = [t[0] if t else "" for t in toks]
    emph = [bool(NUMBER.search(w["text"])) for w in words]
    for seq in seqs:
        for i in range(len(flat) - len(seq) + 1):
            if flat[i:i + len(seq)] == seq:
                for k in range(i, i + len(seq)):
                    emph[k] = True
    for w, e in zip(words, emph):
        w["emph"] = e


def sentence_time(work, ch, sid):
    """Where a sentence starts, in chapter time. None if it isn't in this chapter."""
    sents = load_json(work / "sentences.json")["sentences"]
    ids = [s["id"] for s in sents]
    s = sents[parse_sid(sid, len(ids))]
    first = s["words"][0][1]
    for a, b in ch["keep_source"]:
        if a - 0.3 <= first <= b:
            return max(0.0, Timeline(ch["keep_source"]).rough(first))
    return None


def build(work, n, limit=None):
    work = Path(work)
    chs = load_json(work / "chapters.json")
    ch = chs["chapters"][n - 1]
    plan_p = work / "graphics.plan.json"
    plan = load_json(plan_p) if plan_p.exists() else {}
    rules = voice_rules(work)
    name = (work / "brand.txt").read_text().strip() if (work / "brand.txt").exists() else "default"
    brand_dir = ROOT / "brands" / name
    brand = load_json(brand_dir / "brand.json")

    fail = []
    intro = plan.get("intro") if n == 1 else None
    if intro:
        for k in ("eyebrow", "title", "sub"):
            for p in title_problems(intro.get(k, ""), rules):
                if "longer than" not in p:
                    fail.append(f'Intro {k} "{intro.get(k)}": {p}.')

    callouts = []
    for c in plan.get("callouts", []):
        if c.get("chapter") != n:
            continue
        for k in ("label", "value"):
            for p in title_problems(str(c.get(k, "")), rules):
                fail.append(f'Callout {k} "{c.get(k)}": {p}.')
        t = sentence_time(work, ch, c["at"])
        if t is None:
            fail.append(f"Callout at {c['at']} isn't in chapter {n}.")
            continue
        callouts.append({"start": round(t, 3), "duration": float(c.get("duration", CALLOUT_DEFAULT)),
                         "label": c["label"], "value": str(c["value"])})
    if fail:
        for f in fail:
            print(f"FAIL  {f}", file=sys.stderr)
        return 1

    duration = ch["duration"] if limit is None else min(ch["duration"], limit)
    words = [{"text": w["word"], "start": w["start"], "end": w["end"]} for w in ch["words"]]
    mark_emphasis(words, plan.get("emphasis", []))
    data = {
        "duration": round(duration, 3),
        "intro": intro,
        "chapter": {"n": n, "total": len(chs["chapters"]), "title": ch["title"],
                    "show": not (n == 1 and intro)},
        "words": words,
        "callouts": [c for c in callouts if c["start"] < duration],
        # Not burned in on the long-form video — YouTube gets captions.srt, which the
        # viewer can turn off (hard rule 8). The LinkedIn clip still burns them in.
        "captions": False,
        "brand": {k: brand.get(k) for k in ("colors", "gradient", "fonts")},
    }

    out = work / "graphics" / f"{n:02d}"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(TEMPLATE / "lib", out / "lib")
    shutil.copytree(brand_dir / "fonts", out / "lib" / "fonts")
    html = (TEMPLATE / "chapter.html").read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False))
    html = html.replace('data-start="0" data-duration="10"',
                        f'data-start="0" data-duration="{data["duration"]}"')
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{data['duration']} {len(words)} {sum(w['emph'] for w in words)} "
          f"{len(data['callouts'])} {int(bool(intro))}")
    return 0


def main():
    a = sys.argv
    if len(a) >= 4 and a[1] == "build":
        limit = float(a[a.index("--limit") + 1]) if "--limit" in a else None
        return build(a[2], int(a[3]), limit)
    print("\n".join(__doc__.strip().splitlines()[-1:]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
