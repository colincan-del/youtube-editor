#!/usr/bin/env python3
"""Stage 5 — the content cut list. Checkpoint 1.

Stage 4 removed what nobody would miss. This stage proposes what a viewer
*would* notice going — tangents, repeats, private asides — to bring the rough
cut to the project's length target: 12-18 minutes unless stage 1 was given
--length (see length_target). It proposes; it does not act. Nothing renders
until Colin has answered.

The judgement is not made in this file. A script cannot tell a tangent from
the point. This does the mechanics on either side of it:

  prepare   the rough cut as numbered sentences, each gap between them marked
            with whether a cut may land there        → sentences.md / .json
  (judge)   Claude reads sentences.md against the outline and writes
            cutlist.proposal.json: sentence ranges, one-line reasons
  finalize  turns each range into exact cut points under the hard rules and
            writes the table Colin approves           → cutlist.md / .json
  approve   records Colin's answer and the locked cut  → locked.json

Whatever the proposal says, these hold:
  * a cut starts and ends in the pause between two sentences (hard rule 2),
    keeping a breath either side of the join
  * both edges land on still picture (hard rule 3). A range that can't is
    shrunk a sentence at a time — never grown — and the change is reported
  * nothing inside a word, checked again against every word before writing

The proposal file:

    {"cuts":  [{"from": "S06", "to": "S07", "reason": "Mic test"}],
     "flags": [{"at": "S14", "note": "Narration thins out here — left in"}]}

    cutlist.py prepare  <work-dir> <icp.md | ->
    cutlist.py finalize <work-dir>
    cutlist.py approve  <work-dir> [--keep 2,5]
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleanup import Motion, check_no_word_split, clock, load_words  # noqa: E402

# ------------------------------------------------------------------ tuning
DEFAULT_LENGTH = "12-18"  # minutes; a project's length.txt overrides it
TAIL_KEEP = 0.25       # breath kept after the last word before a cut, at least
HEAD_KEEP = 0.20       # breath kept before the first word after one
LEAD_CUT = 0.20        # a cut starts this far ahead of the first word it removes
MIN_PAD = 0.08         # a cut edge never comes closer than this to a word it keeps...
CUT_PAD = 0.12         # ...or this to one it removes, so no onset or tail leaks through
STILL_SPAN = 0.10      # the picture must hold still this far either side of an edge
SEARCH_STEP = 0.05
LONG_SENTENCE = 25.0   # past this with no full stop, a long pause counts as a boundary
PAUSE_BOUNDARY = 0.70
JOIN_TIGHT = 0.25      # a join with less breath than this may sound rushed
JOIN_LONG = 1.20       # ...or more than this, like a stall

TERMINAL = re.compile(r"[.?!…][\"'”’)\]]*$")


# ------------------------------------------------------------------ timeline

class Timeline:
    """Source time → rough-cut time, from stage 4's keep list.

    For display only. Every cut is decided, checked and later rendered in
    source time. The rough cut's own clock drifts by up to a frame at every
    splice (about half a second over 29 splices), so a rough-cut timestamp is
    good for Colin to scrub to, and not for cutting on.
    """

    def __init__(self, keeps):
        self.keeps = [tuple(k) for k in keeps]
        self.offsets = []
        acc = 0.0
        for a, b in self.keeps:
            self.offsets.append(acc)
            acc += b - a
        self.duration = acc

    def rough(self, t):
        for (a, b), off in zip(self.keeps, self.offsets):
            if t < a:
                return off              # inside a removed stretch: it lands at the join
            if t <= b:
                return off + (t - a)
        return self.duration

    def kept_between(self, t0, t1):
        return sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in self.keeps)


def subtract(keeps, cuts):
    """Keep ranges minus cut ranges. Both in source time."""
    out = []
    for a, b in keeps:
        pieces = [(a, b)]
        for c0, c1 in cuts:
            nxt = []
            for p0, p1 in pieces:
                if c1 <= p0 or c0 >= p1:
                    nxt.append((p0, p1))
                    continue
                if c0 > p0:
                    nxt.append((p0, c0))
                if c1 < p1:
                    nxt.append((c1, p1))
            pieces = nxt
        out += [[round(p0, 3), round(p1, 3)] for p0, p1 in pieces if p1 - p0 > 0.05]
    return out


# ------------------------------------------------------------------ sentences

def build_sentences(words):
    """Split on full stops. WhisperX punctuates well; its segments do not
    always break on sentences, which is why they are not used."""
    sents, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        split = None
        if nxt is None or TERMINAL.search(w["word"].strip()):
            split = "stop"
        elif (cur[-1]["end"] - cur[0]["start"] > LONG_SENTENCE
              and nxt["start"] - w["end"] >= PAUSE_BOUNDARY):
            split = "pause"             # no punctuation for 25s — take the breath
        if split:
            sents.append({"words": cur, "split": split,
                          "start": cur[0]["start"], "end": cur[-1]["end"],
                          "text": " ".join(x["word"].strip() for x in cur)})
            cur = []
    return sents


def still_at(motion, t):
    return all(motion.is_still(t + d) for d in (-STILL_SPAN, 0.0, STILL_SPAN))


def find_still(motion, want, lo, hi):
    """The still moment in [lo, hi] closest to `want`. None if the picture
    moves through all of it — a cut there would jump."""
    if lo > hi + 1e-9:
        return None
    want = min(max(want, lo), hi)
    n = int((hi - lo) / SEARCH_STEP) + 1
    for s in [want] + sorted((lo + i * SEARCH_STEP for i in range(n)),
                             key=lambda s: abs(s - want)):
        if lo - 1e-9 <= s <= hi + 1e-9 and still_at(motion, s):
            return round(s, 3)
    return None


def boundaries(sents, motion, duration):
    """Where a cut may start, or end, in the pause before sentence k.

    Boundary k sits between sentence k-1 and sentence k. A cut removing
    sentences i..j starts at boundary i and ends at boundary j+1.

    Both edges hug the *following* sentence, because in a screen demo the
    footage after a sentence belongs to it: "let's open Attio" is followed by
    Attio opening. So a cut keeps the footage after the last sentence it keeps,
    and removes the footage after the last sentence it removes. Starting a cut
    straight after the kept sentence would announce Attio and then skip it.
    """
    out = []
    n = len(sents)
    for k in range(n + 1):
        b = {"k": k, "cut_start": None, "cut_end": None, "why": ""}
        if k == 0:
            b["cut_start"] = 0.0        # nothing before it to join to
        if k == n:
            b["cut_end"] = round(duration, 3)
        if 0 < k < n:
            pe, ns = sents[k - 1]["end"], sents[k]["start"]
            gap = ns - pe
            if gap < MIN_PAD + CUT_PAD:
                b["why"] = f"no pause between the sentences ({gap:.2f}s)"
            else:
                want = max(pe + min(TAIL_KEEP, gap / 2), ns - LEAD_CUT)
                b["cut_start"] = find_still(motion, want, pe + MIN_PAD, ns - CUT_PAD)
                b["cut_end"] = find_still(motion, ns - min(HEAD_KEEP, gap / 2),
                                          pe + CUT_PAD, ns - MIN_PAD)
                if b["cut_start"] is None or b["cut_end"] is None:
                    b["why"] = "the picture is moving through the pause"
        out.append(b)
    return out


# ------------------------------------------------------------------ held-back names

NAME_SEQ = re.compile(r"\b[A-Z][A-Za-z.'’]+(?: (?:St\.|[A-Z][A-Za-z.'’]+))+")
NAME_ONE = re.compile(r"^[A-Z][A-Za-z.'’]+(?: (?:St\.|[A-Z][A-Za-z.'’]+))*$")


def split_icp(text):
    """Hard rule 4: the cleared / held-back split must be readable, or stop."""
    cleared = re.search(r"^###\s+Published on the site.*cleared for use.*$", text, re.M | re.I)
    held = re.search(r"^###\s+Held back.*$", text, re.M | re.I)
    if not cleared or not held or held.start() < cleared.start():
        return None, None
    cleared_txt = text[cleared.end():held.start()]
    held_txt = re.split(r"^#{1,3}\s", text[held.end():], maxsplit=1, flags=re.M)[0]
    return cleared_txt, held_txt


def held_back_names(icp_text):
    """Proper names in the held-back list that the cleared list doesn't also carry.

    A spoken name is not a proof claim, but it is public once the video is. An
    open deal or an uncleared client named on camera is exactly what the
    held-back list exists to keep off the internet.
    """
    cleared, held = split_icp(icp_text)
    if held is None:
        return None
    terms = set()
    for bold in re.findall(r"\*\*(.+?)\*\*", held):
        for part in re.split(r"[()/,—–]| - ", bold):
            part = part.strip()
            if len(part) > 2 and NAME_ONE.match(part):
                terms.add(part)
    terms.update(NAME_SEQ.findall(held))
    clean = set()
    for t in terms:
        t = re.sub(r"['’]s$", "", t.strip())
        if t and t.lower() not in cleared.lower():
            clean.add(t)
    return sorted(clean)


MONEY = re.compile(r"\$\d[\d,.]*(?:\s?[–-]\s?\$?\d[\d,.]*)?\s?[KMB]?\+?")

def held_back_figures(icp_text):
    """Dollar figures in the held-back list that the cleared list doesn't also
    carry — "$30–80K", an unpublished engagement value. None if unreadable."""
    cleared, held = split_icp(icp_text)
    if held is None:
        return None
    return sorted({m.strip() for m in MONEY.findall(held) if m.strip() not in cleared})

def tok(s):
    return re.sub(r"['’]s$", "", re.sub(r"[^a-z0-9'’]", "", s.lower()))


# Words of a held-back name that say nothing on their own.
GENERIC = {"corp", "inc", "ltd", "llc", "co", "group", "the"}


def find_mentions(sents, names):
    """Loose on purpose: WhisperX heard "Acme Corp" as "Acme core". The
    first word must match; each word after it only its first three letters.

    One word of the name on its own counts too: people on a call say "Acme"
    and "Jane", not "Acme Corp" and "Jane Smith". The price is the odd
    false alarm on a common first name — a miss is worse."""
    hits = []
    for si, s in enumerate(sents):
        toks = [tok(w["word"]) for w in s["words"]]
        for name in names:
            nt = [tok(x) for x in name.split()]
            span = None
            for i in range(len(toks) - len(nt) + 1):
                if toks[i] == nt[0] and all(
                        toks[i + m] == nt[m] or (len(nt[m]) > 3 and toks[i + m][:3] == nt[m][:3])
                        for m in range(1, len(nt))):
                    span = (i, i + len(nt))
                    break
            if span is None and len(nt) > 1:
                for word in nt:
                    if len(word) > 2 and word not in GENERIC and word in toks:
                        i = toks.index(word)
                        span = (i, i + 1)
                        break
            if span:
                hits.append({"sentence": si, "name": name,
                             "heard": " ".join(w["word"].strip()
                                               for w in s["words"][span[0]:span[1]])})
    return hits


# ------------------------------------------------------------------ helpers

def sid(i):
    return f"S{i + 1:02d}"


def parse_sid(v, n):
    m = re.fullmatch(r"\s*[Ss]?0*(\d+)\s*", str(v))
    if not m:
        raise ValueError(f"'{v}' is not a sentence number")
    i = int(m.group(1)) - 1
    if not 0 <= i < n:
        raise ValueError(f"{v} is out of range — there are {n} sentences")
    return i


def mmss(t):
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{t % 60:04.1f}"


def quote(text, limit=140):
    text = text.strip()
    if len(text) <= limit:
        return text
    words = text.split()
    head, tail = [], []
    for w in words:
        if len(" ".join(head + [w])) > limit * 0.62:
            break
        head.append(w)
    for w in reversed(words[len(head):]):
        if len(" ".join([w] + tail)) > limit * 0.3:
            break
        tail.insert(0, w)
    return " ".join(head) + " … " + " ".join(tail)


def md_cell(s):
    return s.replace("|", "\\|").replace("\n", " ")


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ prepare

def prepare(work, icp_path):
    work = Path(work)
    cleanup = load_json(work / "cleanup.json")
    duration = float(cleanup["duration_in"])
    tl = Timeline(cleanup["keep"])
    motion = Motion(work / "motion.json")

    icp_text = "" if icp_path == "-" else Path(icp_path).read_text(encoding="utf-8")
    names = held_back_names(icp_text) if icp_text else []
    if names is None:
        print(f"FAIL  {icp_path} no longer has the \"Published on the site — cleared for use\" "
              "and \"Held back\" sections this stage reads.", file=sys.stderr)
        print("      Hard rule 4: stop and ask rather than guess what is cleared.", file=sys.stderr)
        return 1

    words = load_words(work / "transcript.corrected.json")
    # Words stage 4 already removed (a filler, an abandoned take) are not in the rough cut.
    words = [w for w in words if tl.kept_between(w["start"], w["end"]) >= 0.5 * (w["end"] - w["start"])]
    if not words:
        print("FAIL  No words survive into the rough cut. Nothing to propose.", file=sys.stderr)
        return 1

    sents = build_sentences(words)
    bounds = boundaries(sents, motion, duration)
    mentions = find_mentions(sents, names)

    out_sents = []
    for i, s in enumerate(sents):
        prev_end = sents[i - 1]["end"] if i else 0.0
        out_sents.append({
            "id": sid(i), "start": round(s["start"], 3), "end": round(s["end"], 3),
            "rough_start": round(tl.rough(s["start"]), 3), "rough_end": round(tl.rough(s["end"]), 3),
            "quiet_before": round(tl.rough(s["start"]) - tl.rough(prev_end), 2),
            "split": s["split"], "text": s["text"],
            "words": [[w["word"].strip(), w["start"], w["end"]] for w in s["words"]],
        })

    outline = (work / "outline.md")
    outline_txt = outline.read_text(encoding="utf-8").strip() if outline.exists() else ""

    (work / "sentences.json").write_text(json.dumps({
        "duration": round(duration, 3), "rough_duration": round(tl.duration, 3),
        "motion_aware": motion.ok, "sentences": out_sents, "boundaries": bounds,
        "held_back_names": names, "mentions": mentions,
    }, indent=1), encoding="utf-8")

    write_sentences_md(work / "sentences.md", work.name, out_sents, bounds, tl, outline_txt,
                       names, mentions, motion.ok, length_target(work))

    legal = sum(1 for b in bounds[1:-1] if b["cut_start"] is not None and b["cut_end"] is not None)
    print(f"{len(sents)} {round(tl.duration, 1)} {legal} {max(0, len(bounds) - 2)} "
          f"{len(mentions)} {1 if outline_txt else 0}")
    return 0


def length_target(work):
    """(min, max) seconds the finished video should land in, or None for no
    target. From work/<p>/length.txt, which stage 1's --length writes: "12-18"
    in minutes, or "full" — a product walkthrough keeps everything that works,
    so only what's broken, repeated or off-topic goes."""
    p = Path(work) / "length.txt"
    v = p.read_text().strip() if p.exists() else ""
    if v == "full":
        return None
    lo, hi = (v or DEFAULT_LENGTH).split("-")
    return float(lo) * 60, float(hi) * 60


def minutes(t):
    return f"{t / 60:g}"


def write_sentences_md(path, project, sents, bounds, tl, outline, names, mentions, motion_ok, tgt):
    rough = tl.duration
    if tgt is None:
        target = ("Full walkthrough — no length target. Cut only what's broken, repeated or "
                  "off-topic; everything that works stays.")
    elif rough > tgt[1]:
        mid = sum(tgt) / 2
        target = (f"Over the {minutes(tgt[0])}–{minutes(tgt[1])} minute target. At least "
                  f"**{clock(rough - tgt[1])}** has to go to reach {clock(tgt[1])}; about "
                  f"{clock(rough - mid)} to land near {clock(mid)}.")
    elif rough >= tgt[0]:
        target = (f"Already inside the {minutes(tgt[0])}–{minutes(tgt[1])} minute target. "
                  "Cut only what hurts the video.")
    else:
        target = (f"Under {minutes(tgt[0])} minutes before any content cut. Length is not a "
                  "reason to cut here — cut only what hurts the video.")
    by_sent = {}
    for m in mentions:
        by_sent.setdefault(m["sentence"], []).append(m)

    L = [f"# Sentences — {project}", "",
         "Working file for stage 5. Read it against the outline and write "
         "`cutlist.proposal.json` next to it. See the top of `scripts/cutlist.py` for the format.",
         "",
         f"**Rough cut:** {clock(rough)} · {len(sents)} sentences · {target}", ""]
    if not motion_ok:
        L += ["⚠️ **No motion data** — every boundary reads as still picture. Hard rule 3 is not "
              "being checked. Re-run stage 4.", ""]
    L += ["## Outline", ""]
    L += [outline if outline else "⚠️ **No outline.** Nothing to judge a tangent against.", ""]

    L += ["## How to judge", "",
          "- The outline is the spine. Something on it stays even if it's slow; something off it "
          "is a candidate.",
          "- Cut: tangents, repeats of a point already made, mic checks and meta-talk about the "
          "recording, private asides, anything naming a held-back name.",
          "- Keep: the demo itself even when the narration thins — the screen is the content.",
          "- Prefer whole passages to a string of sentence-sized nicks. One cut per idea.",
          "- A range must start where **Before** is ✓ and end where **After** is ✓. The script "
          "shrinks a range that doesn't, and tells Colin.",
          "- Everything is defaulted to approved and Colin skims. The reason is one line he can "
          "agree with in a second.", ""]

    L += ["## Held-back names", ""]
    if mentions:
        for m in mentions:
            L.append(f"- ⚠️ **{sents[m['sentence']]['id']}** says \"{m['heard']}\" — held back as "
                     f"\"{m['name']}\". Cut it, or flag it for Colin.")
    else:
        L.append("None heard.")
    L += ["", f"Watching for: {', '.join(names) if names else '(the held-back list names nobody)'}", ""]

    L += ["## Sentences", "",
          "**At** is rough-cut time (`cleaned.mp4`). **Quiet** is footage with no speech before "
          "the sentence. It belongs to the sentence *before* it — the screen showing what was "
          "just said — so a cut starting here keeps it, and a cut ending just before here "
          "removes it. **Before / After**: can a cut begin just before this sentence / end just "
          "after it.", "",
          "| # | At | Len | Quiet | Before | After | Said |", "|---|---|---|---|---|---|---|"]
    for i, s in enumerate(sents):
        before = "✓" if bounds[i]["cut_start"] is not None else "✗"
        after = "✓" if bounds[i + 1]["cut_end"] is not None else "✗"
        quiet = f"{s['quiet_before']:.1f}s" if s["quiet_before"] >= 2.0 else ""
        warn = " ⚠️" if i in by_sent else ""
        note = " *(split at a pause, no full stop)*" if s["split"] == "pause" else ""
        L.append(f"| {s['id']}{warn} | {mmss(s['rough_start'])} "
                 f"| {s['rough_end'] - s['rough_start']:.1f}s | {quiet} | {before} | {after} "
                 f"| {md_cell(s['text'])}{note} |")
    blocked = [(i, b) for i, b in enumerate(bounds) if 0 < i < len(bounds) - 1 and b["why"]]
    if blocked:
        L += ["", "## Boundaries a cut can't use", ""]
        for i, b in blocked:
            L.append(f"- Between {sents[i - 1]['id']} and {sents[i]['id']}: {b['why']}.")
    L.append("")
    Path(path).write_text("\n".join(L), encoding="utf-8")


# ------------------------------------------------------------------ finalize

def finalize(work):
    work = Path(work)
    data = load_json(work / "sentences.json")
    cleanup = load_json(work / "cleanup.json")
    tl = Timeline(cleanup["keep"])
    all_words = load_words(work / "transcript.corrected.json")
    sents, bounds = data["sentences"], data["boundaries"]
    n = len(sents)

    try:
        proposal = load_json(work / "cutlist.proposal.json")
    except (OSError, ValueError) as e:
        print(f"FAIL  Couldn't read cutlist.proposal.json: {e}", file=sys.stderr)
        return 1

    problems, cuts, dropped, adjusted = [], [], [], []
    for pi, p in enumerate(proposal.get("cuts", []), 1):
        try:
            i, j = parse_sid(p["from"], n), parse_sid(p.get("to", p["from"]), n)
        except (KeyError, ValueError) as e:
            problems.append(f"proposed cut {pi}: {e}")
            continue
        if j < i:
            i, j = j, i
        reason = str(p.get("reason", "")).strip()
        if not reason:
            problems.append(f"proposed cut {pi} ({sid(i)}–{sid(j)}) has no reason")
            continue
        oi, oj = i, j
        while i <= j and bounds[i]["cut_start"] is None:
            i += 1
        while j >= i and bounds[j + 1]["cut_end"] is None:
            j -= 1
        if i > j:
            dropped.append({"from": sid(oi), "to": sid(oj), "reason": reason,
                            "why": bounds[oi]["why"] or bounds[oj + 1]["why"]
                            or "no still picture at any sentence boundary inside it"})
            continue
        change = []
        if i != oi:
            change.append(f"starts at {sid(i)} instead of {sid(oi)} — "
                          f"{bounds[oi]['why'] or 'no clean edge'} before {sid(oi)}")
        if j != oj:
            change.append(f"ends at {sid(j)} instead of {sid(oj)} — "
                          f"{bounds[oj + 1]['why'] or 'no clean edge'} after {sid(oj)}")
        a, b = bounds[i]["cut_start"], bounds[j + 1]["cut_end"]
        cuts.append({"from": sid(i), "to": sid(j), "i": i, "j": j,
                     "start": a, "end": b, "reason": reason, "adjusted": change})
        if change:
            adjusted.append((len(cuts), change))

    if problems:
        for pr in problems:
            print(f"FAIL  {pr}", file=sys.stderr)
        print("      Fix cutlist.proposal.json and run the stage again.", file=sys.stderr)
        return 1

    cuts.sort(key=lambda c: c["start"])
    for x, y in zip(cuts, cuts[1:]):
        if y["i"] <= x["j"]:
            print(f"FAIL  Proposed cuts {x['from']}–{x['to']} and {y['from']}–{y['to']} overlap. "
                  "Merge them into one.", file=sys.stderr)
            return 1

    # The hard rules, checked again on the final numbers rather than trusted.
    motion = Motion(work / "motion.json")
    bad = check_no_word_split([{"start": c["start"], "end": c["end"]} for c in cuts], all_words)
    if bad:
        c, w = bad[0]
        print(f'FAIL  A cut at {clock(c["start"])} would land inside "{w["word"]}". '
              "Refusing to cut mid-word.", file=sys.stderr)
        return 1
    for c in cuts:
        for edge, t in (("start", c["start"]), ("end", c["end"])):
            if (edge == "start" and c["i"] == 0) or (edge == "end" and c["j"] == n - 1):
                continue                # the video's own start or end — nothing to join
            if not still_at(motion, t):
                print(f"FAIL  Cut {c['from']}–{c['to']} would {edge} while the picture moves "
                      f"({clock(t)}).", file=sys.stderr)
                return 1

    rough = tl.duration
    for num, c in enumerate(cuts, 1):
        c["n"] = num
        c["rough_start"] = round(tl.rough(c["start"]), 3)
        c["rough_end"] = round(tl.rough(c["end"]), 3)
        c["seconds"] = round(tl.kept_between(c["start"], c["end"]), 3)
        c["text"] = " ".join(s["text"] for s in sents[c["i"]:c["j"] + 1])
        prev_end = sents[c["i"] - 1]["end"] if c["i"] > 0 else None
        next_start = sents[c["j"] + 1]["start"] if c["j"] + 1 < n else None
        # What plays either side of the join, without speech. Before it: footage
        # kept with the last kept sentence. After it: lead-in to the next one.
        c["join_before"] = round(tl.kept_between(prev_end, c["start"]), 2) if prev_end is not None else None
        c["join_after"] = round(tl.kept_between(c["end"], next_start), 2) if next_start is not None else None
        c["join_pause"] = (round(c["join_before"] + c["join_after"], 2)
                           if prev_end is not None and next_start is not None else None)
        c["kept_before"] = sents[c["i"] - 1]["text"] if c["i"] > 0 else None
        c["resumes_at"] = sents[c["j"] + 1]["text"] if c["j"] + 1 < n else None
        c["status"] = "proposed"

    # From what is left, not the sum of the cuts: two cuts either side of one
    # pause both claim it.
    after = sum(b - a for a, b in subtract(cleanup["keep"], [(c["start"], c["end"]) for c in cuts]))
    removed = rough - after
    covered = {k for c in cuts for k in range(c["i"], c["j"] + 1)}
    mentions = data.get("mentions", [])
    flags = []
    for f in proposal.get("flags", []):
        try:
            at = parse_sid(f.get("at", ""), n)
            flags.append({"at": sid(at), "rough": sents[at]["rough_start"],
                          "note": str(f.get("note", "")).strip()})
        except ValueError as e:
            print(f"WARN  Ignoring a flag: {e}", file=sys.stderr)

    result = {
        "project": work.name,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "rough_duration": round(rough, 3),
        "proposed_duration": round(after, 3),
        "target": length_target(work),
        "cuts": [{k: v for k, v in c.items() if k not in ("i", "j")} for c in cuts],
        "could_not_make": dropped,
        "flags": flags,
        "held_back_mentions": [dict(m, id=sents[m["sentence"]]["id"],
                                    cut=m["sentence"] in covered) for m in mentions],
        "approved": None,
    }
    (work / "cutlist.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    write_cutlist_md(work / "cutlist.md", result, sents)

    open_mentions = sum(1 for m in result["held_back_mentions"] if not m["cut"])
    print(f"{len(cuts)} {round(removed, 1)} {round(rough, 1)} {round(after, 1)} "
          f"{len(adjusted)} {len(dropped)} {open_mentions}")
    for c in cuts:
        print(f"CUT {c['n']} {c['from']}-{c['to']} source={c['start']:.2f}-{c['end']:.2f} "
              f"rough={mmss(c['rough_start'])} {c['seconds']:.1f}s :: {c['reason']}")
    return 0


def write_cutlist_md(path, r, sents):
    rough, after, cuts = r["rough_duration"], r["proposed_duration"], r["cuts"]
    L = [f"# Cut list — {r['project']}", "",
         "**Checkpoint 1.** Nothing has been cut yet. Every cut below happens unless you say "
         "otherwise.", ""]
    if cuts:
        L.append(f"**{clock(rough)} → {clock(after)}** with all {len(cuts)} cut"
                 f"{'s' if len(cuts) != 1 else ''}, {clock(rough - after)} removed.")
    else:
        L.append(f"**No content cuts proposed.** The rough cut stays at {clock(rough)}.")
    tgt = r["target"]
    if tgt is None:
        L.append("Full walkthrough — no length target. These cuts are about what's broken, "
                 "repeated or off-topic, not length.")
    elif after > tgt[1]:
        L.append(f"⚠️ Still over the {minutes(tgt[1])}-minute target by {clock(after - tgt[1])}.")
    elif rough < tgt[0]:
        L.append(f"The rough cut was under the {minutes(tgt[0])}–{minutes(tgt[1])} minute target "
                 "before any cut, so these are about what belongs in the video, not about length.")
    elif after < tgt[0]:
        L.append(f"⚠️ This takes it under the {minutes(tgt[0])}-minute floor by "
                 f"{clock(tgt[0] - after)}.")
    else:
        L.append(f"Inside the {minutes(tgt[0])}–{minutes(tgt[1])} minute target.")
    L += ["", "**To answer:** say \"approve\", or name any cut you want to keep in — "
          "\"keep 2\". Times are in the rough cut (`cleaned.mp4`), within about half a second.", ""]

    if cuts:
        L += ["| # | At | Length | What's said | Why |", "|---|---|---|---|---|"]
        for c in cuts:
            L.append(f"| {c['n']} | {mmss(c['rough_start'])} | {c['seconds']:.1f}s "
                     f"| \"{md_cell(quote(c['text']))}\" | {md_cell(c['reason'])} |")
        L.append("")

    def tail(s):
        w = s.split()
        return ("…" if len(w) > 6 else "") + " ".join(w[-6:])

    def head(s):
        w = s.split()
        return " ".join(w[:6]) + ("…" if len(w) > 6 else "")

    notes = []
    for c in cuts:
        for ch in c["adjusted"]:
            notes.append(f"- **Cut {c['n']}** {ch}.")
        jp, jb, ja = c.get("join_pause"), c.get("join_before"), c.get("join_after")
        if jp is not None and jp < JOIN_TIGHT:
            notes.append(f"- **Cut {c['n']}** leaves only {jp:.2f}s of breath at the join. "
                         "Listen to it before approving.")
        if jb is not None and jb > JOIN_LONG:
            notes.append(f"- **Cut {c['n']}** keeps {jb:.1f}s of screen without speech after "
                         f"\"{md_cell(tail(c['kept_before']))}\" — what that sentence was showing.")
        if ja is not None and ja > JOIN_LONG:
            notes.append(f"- **Cut {c['n']}** resumes {ja:.1f}s before \"{md_cell(head(c['resumes_at']))}\" "
                         "— the picture was moving any closer in.")
    if notes:
        L += ["## At the joins", ""] + notes + [""]

    if r["could_not_make"]:
        L += ["## Couldn't make", "",
              "Proposed, but there was no clean place to cut. Left in.", ""]
        for d in r["could_not_make"]:
            L.append(f"- {d['from']}–{d['to']}: {d['reason']} — {d['why']}.")
        L.append("")

    hb = r["held_back_mentions"]
    if hb:
        L += ["## Held-back names", ""]
        for m in hb:
            where = mmss(sents[m["sentence"]]["rough_start"])
            if m["cut"]:
                L.append(f"- \"{m['heard']}\" at {where} — inside a cut above.")
            else:
                L.append(f"- ⚠️ **\"{m['heard']}\" at {where} is NOT cut.** It is on the held-back "
                         "list and would be public. Say if it can stay.")
        L.append("")

    if r["flags"]:
        L += ["## Noticed, left in", ""]
        for f in r["flags"]:
            L.append(f"- {mmss(f['rough'])} — {f['note']}")
        L.append("")
    Path(path).write_text("\n".join(L), encoding="utf-8")


# ------------------------------------------------------------------ approve

def approve(work, keep_arg):
    work = Path(work)
    r = load_json(work / "cutlist.json")
    cleanup = load_json(work / "cleanup.json")
    all_words = load_words(work / "transcript.corrected.json")
    nums = {c["n"] for c in r["cuts"]}
    keep = set()
    if keep_arg:
        for part in re.split(r"[,\s]+", keep_arg.strip()):
            if not part:
                continue
            if not part.isdigit() or int(part) not in nums:
                print(f"FAIL  There is no cut {part}. The cuts are "
                      f"{', '.join(str(x) for x in sorted(nums)) or 'none'}.", file=sys.stderr)
                return 1
            keep.add(int(part))

    for c in r["cuts"]:
        c["status"] = "kept in" if c["n"] in keep else "approved"
    approved = [c for c in r["cuts"] if c["status"] == "approved"]
    keep_src = subtract(cleanup["keep"], [(c["start"], c["end"]) for c in approved])

    # Every removal in the locked cut — stage 4's and these — checked once more as a whole.
    gaps, cur = [], 0.0
    for a, b in keep_src:
        if a > cur + 1e-3:
            gaps.append({"start": cur, "end": a})
        cur = b
    if cleanup["duration_in"] > cur + 1e-3:
        gaps.append({"start": cur, "end": cleanup["duration_in"]})
    bad = check_no_word_split(gaps, all_words)
    if bad:
        c, w = bad[0]
        print(f'FAIL  The locked cut would split "{w["word"]}" at {clock(c["start"])}. '
              "Nothing was locked.", file=sys.stderr)
        return 1

    duration = round(sum(b - a for a, b in keep_src), 3)
    stamp = datetime.now().isoformat(timespec="seconds")
    r["approved"] = {"at": stamp, "kept_in": sorted(keep), "duration": duration}
    (work / "cutlist.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    (work / "locked.json").write_text(json.dumps({
        "project": r["project"], "locked_at": stamp, "duration": duration,
        "render_from": "source.mp4",
        "note": ("keep_source is in source.mp4 time and is what renders. It already includes "
                 "stage 4's cleanup. Do not re-cut cleaned.mp4 by rough-cut time — its clock "
                 "drifts a frame per splice."),
        "keep_source": keep_src,
        "content_cuts": [{k: c[k] for k in ("n", "from", "to", "start", "end", "seconds", "reason")}
                         for c in approved],
    }, indent=1), encoding="utf-8")

    print(f"{len(approved)} {len(keep)} {duration} {len(keep_src)}")
    for c in r["cuts"]:
        print(f"CUT {c['n']} {c['status']} source={c['start']:.2f}-{c['end']:.2f} "
              f"{c['seconds']:.1f}s :: {c['reason']}")
    return 0


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("prepare", "finalize", "approve"):
        print("\n".join(__doc__.strip().splitlines()[-3:]), file=sys.stderr)
        return 2
    cmd, work = sys.argv[1], sys.argv[2]
    if cmd == "prepare":
        if len(sys.argv) < 4:
            print("usage: cutlist.py prepare <work-dir> <icp.md>", file=sys.stderr)
            return 2
        return prepare(work, sys.argv[3])
    if cmd == "finalize":
        return finalize(work)
    keep = ""
    if "--keep" in sys.argv:
        i = sys.argv.index("--keep")
        keep = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
    return approve(work, keep)


if __name__ == "__main__":
    sys.exit(main())
