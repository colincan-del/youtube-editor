#!/usr/bin/env python3
"""Stage 3 — apply the brand's glossary.md to a word-level WhisperX transcript.

Two things this deliberately does NOT do:

  * It does not apply the "Voice corrections" table to the transcript. Those
    rewrite what Colin *said* ("project management software" -> "execution
    system"). In a caption file that desyncs from the audio and a viewer
    reading along sees the mismatch. They are positioning rules for generated
    copy — title, chapter names, description — so they are written out to
    voice-rules.json for stages 6 and 10 to enforce instead.

  * It does not touch anything in "Terms to leave alone". Those are listed in
    the glossary precisely because a loose correction would damage them.

Everything else in the Heard-as/Correct tables is applied over the word array,
timings preserved, and every single change is written to a report Colin can
skim.
"""

import difflib
import json
import re
import sys
from pathlib import Path

# ------------------------------------------------------------------ parsing

ROW = re.compile(r"^\|(.+)\|\s*$")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def unbold(s):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s).strip()


def parse_glossary(path):
    """Read the markdown into: corrections, voice rules, banned, protected."""
    corrections = []          # (variant, replacement, section)
    voice_rules = []          # (never, always)
    banned = []
    protected = []
    section = ""

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("#"):
            section = line.lstrip("#").strip()
            i += 1
            continue

        m = ROW.match(line)
        if m and i + 1 < len(lines) and set(lines[i + 1].strip()) <= set("|-: "):
            header = [c.lower() for c in cells(line)]
            i += 2
            kind = None
            if "heard as" in header and "correct" in header:
                kind = "correction"
            elif "never output" in header:
                kind = "voice"
            while i < len(lines) and ROW.match(lines[i]):
                row = cells(lines[i])
                i += 1
                if len(row) < 2:
                    continue
                left, right = row[0], row[1]
                # "(leave as spoken — all valid)" and friends: no replacement.
                if right.startswith("*(") or right.startswith("("):
                    continue
                if kind == "correction":
                    variants = [unbold(v) for v in left.split("·") if unbold(v)]
                    targets = [unbold(t) for t in right.split(" / ") if unbold(t)]
                    if not targets:
                        continue
                    for v in variants:
                        corrections.append((v, best_target(v, targets), section))
                elif kind == "voice":
                    for v in (unbold(x) for x in left.split("·")):
                        if v:
                            voice_rules.append((v, unbold(right)))
            continue

        if section.lower().startswith("terms to leave alone") and line.strip() and not line.startswith(">"):
            if "·" in line:
                protected += [w.strip() for w in line.split("·") if w.strip()]

        if section.lower().startswith("banned") and "·" in line:
            for w in line.split("·"):
                w = unbold(w).strip()
                w = re.sub(r"\*\(.*?\)\*", "", w).strip()
                if w and not w.startswith("Never"):
                    banned.append(w)

        i += 1

    return corrections, voice_rules, banned, protected


def trim_variant(vt, target):
    """`lookahead schedule` -> `lookahead` would silently eat the word "schedule".

    When the target is just the variant's opening words run together, only
    consume those words and leave the rest of the sentence alone.
    """
    flat = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    goal = flat(target)
    for k in range(1, len(vt)):
        if flat("".join(vt[:k])) == goal:
            return vt[:k]
    return vt


def best_target(variant, targets):
    """`RFI / RFIs` is two targets for six variants. Pick the closer one.

    'R F Is' -> RFIs, 'R F I' -> RFI, '3 week look ahead' -> three-week lookahead.
    """
    if len(targets) == 1:
        return targets[0]
    key = norm(variant).replace(" ", "")
    return max(targets, key=lambda t: difflib.SequenceMatcher(
        None, key, norm(t).replace(" ", "")).ratio())


# ------------------------------------------------------------------ matching

PUNCT = " \t\n\r.,!?;:\"'()[]{}…—–"


def norm(s):
    return re.sub(r"\s+", " ", s.lower().strip(PUNCT)).strip()


def tokens(s):
    return [t for t in (norm(w) for w in s.split()) if t]


def trailing_punct(word):
    m = re.search(r"[.,!?;:…]+$", word)
    return m.group(0) if m else ""


def load_dict():
    try:
        return {w.strip().lower() for w in
                open("/usr/share/dict/words", encoding="utf-8", errors="ignore")}
    except OSError:
        return set()


# ------------------------------------------------------------------ applying

def apply_to_words(words, rules, report, seg_index):
    """Rewrite a segment's word list in place-ish; returns the new list.

    Timings are preserved: a replacement spans exactly the same start..end as
    the words it replaced, so no cut decision downstream moves because of a
    spelling fix.
    """
    out = []
    i = 0
    n = len(words)
    while i < n:
        hit = None
        for length, variant_tokens, replacement, section, ambiguous in rules:
            if i + length > n:
                continue
            window = [norm(words[i + k].get("word", "")) for k in range(length)]
            if window == variant_tokens:
                hit = (length, replacement, section, ambiguous)
                break
        if not hit:
            out.append(words[i])
            i += 1
            continue

        length, replacement, section, ambiguous = hit
        matched = words[i:i + length]
        heard = " ".join(w.get("word", "") for w in matched)
        tail = trailing_punct(matched[-1].get("word", ""))

        starts = [w.get("start") for w in matched if w.get("start") is not None]
        ends = [w.get("end") for w in matched if w.get("end") is not None]
        span_start = min(starts) if starts else None
        span_end = max(ends) if ends else None
        scores = [w.get("score") for w in matched if isinstance(w.get("score"), (int, float))]
        score = min(scores) if scores else None

        parts = replacement.split()
        parts[-1] = parts[-1] + tail
        new_words = distribute(parts, span_start, span_end, score)
        out.extend(new_words)

        # A rule can match text that was already spelled correctly. It changed
        # nothing, so it does not belong in a report Colin is meant to skim.
        if " ".join(w["word"] for w in new_words) == heard:
            i += length
            continue

        report.append({
            "segment": seg_index,
            "start": span_start,
            "heard": heard,
            "corrected": replacement,
            "section": section,
            "review": ambiguous,
        })
        i += length
    return out


def distribute(parts, start, end, score):
    """Spread N replacement tokens across one time span, by character length."""
    if start is None or end is None or len(parts) == 1:
        return [{"word": p, "start": start, "end": end, "score": score} for p in parts]
    total = sum(len(p) for p in parts) or 1
    span = end - start
    out = []
    cursor = start
    for p in parts:
        share = span * (len(p) / total)
        out.append({"word": p, "start": round(cursor, 3),
                    "end": round(cursor + share, 3), "score": score})
        cursor += share
    out[-1]["end"] = end
    return out


def rebuild_text(words):
    text = ""
    for w in words:
        token = w.get("word", "")
        if not token:
            continue
        text += token if (text.endswith(" ") or not text) else " " + token
    return text.strip()


# ------------------------------------------------------------------ main

def main():
    if len(sys.argv) < 4:
        print("usage: glossary.py <glossary.md> <transcript.json> <out-dir>", file=sys.stderr)
        return 2
    gloss_path, transcript_path, outdir = sys.argv[1], sys.argv[2], Path(sys.argv[3])

    corrections, voice_rules, banned, protected = parse_glossary(gloss_path)
    # A glossary with correction rows that parse to nothing has changed shape —
    # stop. One with no rows at all is a new brand that has none yet — fine.
    rows = [l for l in Path(gloss_path).read_text(encoding="utf-8").splitlines()
            if l.startswith("|") and not re.match(r"^\|[\s|:-]+\|?$", l)
            and "heard as" not in l.lower() and "never" not in l.lower()]
    if not corrections and rows:
        print(f"FAIL  Couldn't read a single correction out of {gloss_path}.",
              file=sys.stderr)
        print("      Its table structure has changed. Stopping rather than guessing.",
              file=sys.stderr)
        return 1

    english = load_dict()
    protected_norm = {norm(p) for p in protected}

    rules = []
    seen = set()
    for variant, replacement, section in corrections:
        vt = tokens(variant)
        if not vt:
            continue
        vt = trim_variant(vt, replacement)
        key = tuple(vt)
        if key in seen:
            continue
        seen.add(key)
        if key in {tuple(tokens(p)) for p in protected}:
            continue  # never touch a term the glossary says to leave alone
        # A one-word variant that is also an ordinary English word ("giant",
        # "gaunt") will fire on innocent sentences. Brand terms are meant to,
        # so they are not flagged; the rest get a line in the report.
        ambiguous = (len(vt) == 1
                     and vt[0] in english
                     and not section.lower().startswith("critical"))
        rules.append((len(vt), vt, replacement, section, ambiguous))
    # Longest match first: "giant chart" must win over "giant".
    rules.sort(key=lambda r: -r[0])

    data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    report = []

    for idx, seg in enumerate(data.get("segments", [])):
        words = seg.get("words")
        if words:
            seg["words"] = apply_to_words(words, rules, report, idx)
            seg["text"] = rebuild_text(seg["words"])
        elif seg.get("text"):
            new = seg["text"]
            for length, vt, replacement, section, ambiguous in rules:
                pattern = r"\b" + r"\s+".join(re.escape(t) for t in vt) + r"\b"
                new, count = re.subn(pattern, replacement.replace("\\", ""),
                                     new, flags=re.IGNORECASE)
                if count:
                    report.append({"segment": idx, "start": seg.get("start"),
                                   "heard": " ".join(vt), "corrected": replacement,
                                   "section": section, "review": ambiguous})
            seg["text"] = new

    if "word_segments" in data:
        data["word_segments"] = [w for s in data.get("segments", [])
                                 for w in s.get("words", [])]

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "transcript.corrected.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    (outdir / "voice-rules.json").write_text(json.dumps({
        "note": "Applied to generated copy — title, chapter names, description. "
                "NOT to the transcript or captions, which stay verbatim.",
        "replace": [{"never": n, "always": a} for n, a in voice_rules],
        "banned": banned,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    write_report(outdir / "glossary-report.md", report, rules, voice_rules, banned)

    flagged = sum(1 for r in report if r["review"])
    print(f"{len(report)} {len(rules)} {flagged}")
    return 0


def clock(t):
    if t is None:
        return "--:--"
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def write_report(path, report, rules, voice_rules, banned):
    lines = ["# Glossary pass", ""]
    if not report:
        lines += ["No corrections were needed. The transcript already spelled "
                  "every term in the glossary correctly.", ""]
    else:
        counts = {}
        for r in report:
            counts[(r["heard"].lower(), r["corrected"])] = \
                counts.get((r["heard"].lower(), r["corrected"]), 0) + 1
        lines += [f"**{len(report)} corrections** from {len(counts)} rules.", ""]

        flagged = [r for r in report if r["review"]]
        if flagged:
            lines += ["## Worth a glance", "",
                      "These came from one-word rules that are also ordinary English "
                      "words, so they can fire on a sentence that meant no such thing. "
                      "Check the context; if one is wrong, narrow the rule in "
                      "`brand/glossary.md`.", "",
                      "| Time | Heard | Became |", "|---|---|---|"]
            for r in flagged:
                lines.append(f"| {clock(r['start'])} | {r['heard']} | {r['corrected']} |")
            lines.append("")

        lines += ["## Every correction", "", "| Time | Heard | Became | From |",
                  "|---|---|---|---|"]
        for r in report:
            lines.append(f"| {clock(r['start'])} | {r['heard']} | {r['corrected']} "
                         f"| {r['section']} |")
        lines.append("")

    lines += ["## Held for later stages", "",
              f"{len(voice_rules)} positioning rules and {len(banned)} banned words were "
              "read out of the glossary and written to `voice-rules.json`. They are "
              "**not** applied to the transcript — rewriting what Colin said would "
              "desync the captions from the audio. They are enforced on the title, "
              "chapter names and description instead.", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
