#!/usr/bin/env python3
"""Stage 4 — decide what unattended cleanup removes.

Three kinds of removal, in ascending order of how much judgement they need:

  dead air   long silences, trimmed back to a natural breath
  filler     standalone "um"/"uh", only where a splice will be inaudible
  flub       a sentence started, abandoned, and started again — the first go

This writes the decisions to cleanup.json and edit notes. It renders nothing;
04-cleanup.sh does that. Keeping the decision separate from the render means a
threshold can be retuned and re-inspected without re-encoding anything.

Hard rules enforced here:
  * cuts land on word boundaries, never inside one
  * cuts land in still picture — a splice inside a Screen Studio zoom jumps

    cleanup.py <transcript.json> <silence.txt> <motion.json> <duration> <out-dir>
"""

import json
import re
import sys
from pathlib import Path

# ------------------------------------------------------------------ tuning
# The shortest pause that registers at all is set where the silences are found,
# in 04-cleanup.sh: silencedetect=noise=-35dB:d=0.35
PAUSE_MAX = 0.90      # anything longer than this gets trimmed back to...
PAUSE_TRIM_TO = 0.45  # ...this much breathing room
MIN_SAVING = 0.25     # don't bother with a cut that saves less than this
EDGE_PAD = 0.08       # keep this much silence either side of a word we cut to
FLUB_MIN_WORDS = 3    # a restart must repeat at least this many words
FLUB_MIN_GAP = 0.25   # ...after a pause at least this long — where he gave up
FLUB_WINDOW = 12.0    # ...and no longer than this, or it is a different take
MOTION_SEARCH = 0.60  # how far a cut may be nudged to find still picture
WORD_GUARD = 0.10     # dead air never comes closer than this to a transcribed word...
WORD_GUARD_WEAK = 0.35  # ...or this, when WhisperX was unsure where the word sits
WEAK_SCORE = 0.50     # alignment score below which a word's timing is not trusted

FILLERS = {"um", "uh", "uhh", "umm", "erm", "er", "hmm", "mm", "mmm", "ah", "eh"}

PUNCT = " \t\n\r.,!?;:\"'()[]{}…—–"


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower().strip(PUNCT))


# ------------------------------------------------------------------ inputs

def load_words(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            if w.get("start") is None or w.get("end") is None:
                continue
            words.append({"word": w.get("word", ""), "start": float(w["start"]),
                          "end": float(w["end"]), "norm": norm(w.get("word", "")),
                          "score": w.get("score")})
    words.sort(key=lambda w: w["start"])
    return words


SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
SILENCE_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def load_silences(path, duration):
    """ffmpeg's silencedetect output -> [(start, end)].

    Read from the log rather than inferred from word gaps: WhisperX pads word
    boundaries, and a gap between words is not proof the room was quiet.
    """
    out = []
    start = None
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        m = SILENCE_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = SILENCE_END.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


class Motion:
    def __init__(self, path):
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = None
        self.ok = bool(d and d.get("scores"))
        if self.ok:
            self.times = d["times"]
            self.scores = d["scores"]
            self.threshold = d["still_threshold"]
            self.step = 1.0 / d.get("fps", 10)

    def score(self, t):
        if not self.ok:
            return 0.0
        i = int(round(t / self.step)) - 1
        i = max(0, min(len(self.scores) - 1, i))
        return self.scores[i]

    def is_still(self, t):
        return not self.ok or self.score(t) <= self.threshold

    def nearest_still(self, t, lo, hi):
        """Nudge a cut to the closest still moment inside [lo, hi]. None if there
        isn't one — the caller then skips the cut rather than making it jump."""
        if not self.ok or self.is_still(t):
            return t
        best, best_d = None, None
        s = lo
        while s <= hi:
            if self.is_still(s):
                d = abs(s - t)
                if best_d is None or d < best_d:
                    best, best_d = s, d
            s += self.step
        return best


# ------------------------------------------------------------------ decisions

def speech_free(silences, words):
    """The parts of each silence that no transcribed word falls in.

    silencedetect judges by level alone, at a fixed -35 dB. Speak quietly —
    muttering while reading the screen — and it calls the speech silence. The
    first real recording lost "Agent Reach is what we're looking" and "and
    running" that way. WhisperX heard those words; its transcript is the
    better witness to whether someone was talking, so a word inside a
    "silence" splits it, and only the gaps either side can be trimmed.
    """
    out, notes = [], []
    for s_start, s_end in silences:
        pieces = [(s_start, s_end)]
        heard = []
        for w in words:
            weak = w.get("score") is not None and w["score"] < WEAK_SCORE
            g = WORD_GUARD_WEAK if weak else WORD_GUARD
            a, b = w["start"] - g, w["end"] + g
            if b <= s_start or a >= s_end:
                continue
            # Every overlap is guarded, but only a word mostly inside the
            # "silence" is worth telling Colin about. A last syllable brushing
            # its edge is two clocks disagreeing, not someone muttering.
            span = max(1e-6, w["end"] - w["start"])
            if min(w["end"], s_end) - max(w["start"], s_start) > 0.5 * span:
                heard.append(w)
            nxt = []
            for p0, p1 in pieces:
                if b <= p0 or a >= p1:
                    nxt.append((p0, p1))
                    continue
                if a > p0:
                    nxt.append((p0, a))
                if b < p1:
                    nxt.append((b, p1))
            pieces = nxt
        if heard and s_end - s_start > PAUSE_MAX:
            said = " ".join(w["word"].strip() for w in heard[:8])
            notes.append(f'Heard quiet speech inside what read as {s_end - s_start:.1f}s of '
                         f'silence at {clock(s_start)} ("{said}") — trimmed only around it.')
        out += [(p0, p1) for p0, p1 in pieces if p1 - p0 > 0.05]
    return out, notes


def find_dead_air(silences, motion, duration, notes):
    """Trim long silences back to a breath, splicing in still picture only.

    `silences` must already have speech taken out of them — see speech_free.
    """
    cuts = []
    for s_start, s_end in silences:
        length = s_end - s_start
        if length <= PAUSE_MAX:
            continue
        # Keep PAUSE_TRIM_TO of quiet, split either side of the join.
        half = PAUSE_TRIM_TO / 2.0
        cut_a = s_start + half
        cut_b = s_end - half
        if cut_b - cut_a < MIN_SAVING:
            continue

        lo, hi = s_start + 0.05, s_end - 0.05
        a = motion.nearest_still(cut_a, max(lo, cut_a - MOTION_SEARCH),
                                 min(hi, cut_a + MOTION_SEARCH))
        b = motion.nearest_still(cut_b, max(lo, cut_b - MOTION_SEARCH),
                                 min(hi, cut_b + MOTION_SEARCH))
        if a is None or b is None or b - a < MIN_SAVING:
            notes.append(f"Left a {length:.1f}s pause at {clock(s_start)} — the picture "
                         "was moving through all of it and a cut there would jump.")
            continue
        cuts.append({"start": round(a, 3), "end": round(b, 3), "kind": "dead air",
                     "reason": f"{length:.1f}s of silence, trimmed to {PAUSE_TRIM_TO:.2f}s"})
    return cuts


def quiet_around(word, index, words, silences):
    """Silence adjacent to a word, in seconds, before and after."""
    before = after = 0.0
    for s_start, s_end in silences:
        if abs(s_end - word["start"]) < 0.12:
            before = max(before, s_end - s_start)
        if abs(s_start - word["end"]) < 0.12:
            after = max(after, s_end - s_start)
    return before, after


def find_fillers(words, silences, motion, notes):
    """Only remove a filler when the splice will be inaudible.

    Cutting "um" out of the middle of a flowing sentence leaves an audible
    clip. It has to have silence on at least one side to hide the join.
    """
    cuts = []
    for i, w in enumerate(words):
        if w["norm"] not in FILLERS:
            continue
        before, after = quiet_around(w, i, words, silences)
        if max(before, after) < 0.12:
            continue
        a = max(0.0, w["start"] - EDGE_PAD)
        b = w["end"] + EDGE_PAD
        if i > 0 and a < words[i - 1]["end"]:
            a = words[i - 1]["end"]        # never eat into the previous word
        if i + 1 < len(words) and b > words[i + 1]["start"]:
            b = words[i + 1]["start"]      # or the next one
        if b - a < 0.08:
            continue
        a2 = motion.nearest_still(a, a - MOTION_SEARCH, b)
        b2 = motion.nearest_still(b, a, b + MOTION_SEARCH)
        if a2 is None or b2 is None or b2 - a2 < 0.08:
            notes.append(f'Left the "{w["word"]}" at {clock(w["start"])} — the picture '
                         "was moving and the cut would have shown.")
            continue
        cuts.append({"start": round(a2, 3), "end": round(b2, 3), "kind": "filler",
                     "reason": f'removed "{w["word"].strip()}"'})
    return cuts


def find_flubs(words, silences, motion, notes):
    """A line started, abandoned, restarted. Keep the last take.

    The hard part is telling a restart from ordinary parallel construction.
    "the one you sold and the one you are actually building" repeats three
    words and is not a flub; cutting from the first "the one you" to the
    second deletes "sold and" and destroys the sentence.

    What separates them is what sits in between. A real restart abandons the
    line and starts again immediately — nothing between the two attempts but a
    breath, or at most a filler. Parallel construction has content in between.
    So the repeat must follow the first attempt directly, and there must be a
    pause where the speaker gave up.

    This misses fast restarts with no pause. That is the right way to be
    wrong: cleanup runs unattended, and a missed flub is a note at checkpoint
    1, while a wrong cut is a sentence with a hole in it.
    """
    cuts = []
    n = len(words)
    i = 0
    while i < n:
        matched = None
        # Longest repeat first, so a six-word restart is not cut as three.
        for span in range(10, FLUB_MIN_WORDS - 1, -1):
            if i + span > n:
                continue
            first = [w["norm"] for w in words[i:i + span] if w["norm"]]
            if len(first) < FLUB_MIN_WORDS:
                continue

            # The retake has to start right after the abandoned attempt.
            # Skip over filler only — "two plans, um, every project..." is
            # still a restart; "two plans, sold and, every project..." is not.
            j = i + span
            while j < n and words[j]["norm"] in FILLERS:
                j += 1
            if j + span > n:
                continue
            if [w["norm"] for w in words[j:j + span] if w["norm"]] != first:
                continue

            gap = words[j]["start"] - words[i + span - 1]["end"]
            if gap < FLUB_MIN_GAP or gap > FLUB_WINDOW:
                continue
            matched = (span, j, gap)
            break

        if not matched:
            i += 1
            continue

        span, j, gap = matched
        a = words[i]["start"] - EDGE_PAD
        b = words[j]["start"] - EDGE_PAD
        if i > 0:
            a = max(a, words[i - 1]["end"] + 0.02)
        a = max(0.0, a)
        if b - a < MIN_SAVING:
            i += span
            continue

        a2 = motion.nearest_still(a, a - MOTION_SEARCH, a + MOTION_SEARCH)
        b2 = motion.nearest_still(b, b - MOTION_SEARCH, b + MOTION_SEARCH)
        phrase = " ".join(w["word"] for w in words[i:i + span]).strip()
        if a2 is None or b2 is None or b2 - a2 < MIN_SAVING:
            notes.append(f'Left the false start at {clock(words[i]["start"])} '
                         f'("{phrase}") — the picture was moving through it.')
            i += span
            continue

        cuts.append({"start": round(a2, 3), "end": round(b2, 3), "kind": "flub",
                     "reason": f'false start — "{phrase}" was abandoned and started '
                               f'again after a {gap:.1f}s pause'})
        i = j
    return cuts


# ------------------------------------------------------------------ assembly

def snap_to_words(cuts, words):
    """Pull every cut boundary back off any word it lands inside.

    Hard rule 2. The boundaries come from three different clocks —
    silencedetect reads the waveform, WhisperX reads the model, the motion
    nudge reads the picture — and they do not agree to the millisecond. Where
    they disagree the cut always shrinks, never grows: removing less is safe,
    clipping the front off a word is not.
    """
    out = []
    for c in cuts:
        a, b = c["start"], c["end"]
        for _ in range(3):
            moved = False
            for w in words:
                if w["end"] <= a + 1e-6 or w["start"] >= b - 1e-6:
                    continue
                if w["start"] < a - 1e-6:          # straddles the start
                    a, moved = w["end"], True
                elif w["end"] > b + 1e-6:          # straddles the end
                    b, moved = w["start"], True
            if not moved:
                break
        if b - a >= MIN_SAVING or (c["kind"] == "filler" and b - a >= 0.08):
            d = dict(c)
            d["start"], d["end"] = round(a, 3), round(b, 3)
            out.append(d)
    return out


def merge(cuts):
    cuts = sorted(cuts, key=lambda c: c["start"])
    out = []
    for c in cuts:
        if out and c["start"] <= out[-1]["end"] + 0.01:
            last = out[-1]
            if c["end"] > last["end"]:
                last["end"] = c["end"]
                if c["kind"] not in last["kind"]:
                    last["kind"] = f"{last['kind']} + {c['kind']}"
                    last["reason"] = f"{last['reason']}; {c['reason']}"
        else:
            out.append(dict(c))
    return out


def to_keeps(cuts, duration):
    keeps = []
    cursor = 0.0
    for c in cuts:
        if c["start"] > cursor + 0.01:
            keeps.append([round(cursor, 3), round(c["start"], 3)])
        cursor = max(cursor, c["end"])
    if duration - cursor > 0.01:
        keeps.append([round(cursor, 3), round(duration, 3)])
    return [k for k in keeps if k[1] - k[0] > 0.05]


def check_dead_air_clear(cuts, words):
    """Dead air removes silence and nothing else. check_no_word_split allows a
    word wholly inside a cut — fillers and false starts need that — so it can't
    see a "silence" that swallowed quiet speech whole. This can."""
    return [(c, w) for c in cuts if c["kind"] == "dead air" for w in words
            if w["start"] < c["end"] - 1e-6 and w["end"] > c["start"] + 1e-6]


def check_no_word_split(cuts, words):
    """Hard rule 2. A boundary inside a word is a bug, not a judgement call."""
    bad = []
    for c in cuts:
        for w in words:
            if w["end"] <= c["start"] or w["start"] >= c["end"]:
                continue
            inside = w["start"] >= c["start"] - 1e-6 and w["end"] <= c["end"] + 1e-6
            if not inside:
                bad.append((c, w))
    return bad


def clock(t):
    t = float(t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


def main():
    if len(sys.argv) < 6:
        print("usage: cleanup.py <transcript> <silence.txt> <motion.json> "
              "<duration> <out-dir>", file=sys.stderr)
        return 2
    transcript, silence_log, motion_json, duration, outdir = sys.argv[1:6]
    duration = float(duration)
    outdir = Path(outdir)

    words = load_words(transcript)
    silences = load_silences(silence_log, duration)
    motion = Motion(motion_json)
    notes = []

    if not words:
        print("FAIL  The transcript has no timed words. Nothing can be cut safely.",
              file=sys.stderr)
        return 1

    quiet, spoken = speech_free(silences, words)
    cuts = (find_flubs(words, silences, motion, notes)
            + find_fillers(words, silences, motion, notes)
            + find_dead_air(quiet, motion, duration, notes))
    cuts = snap_to_words(cuts, words)

    swallowed = check_dead_air_clear(cuts, words)
    if swallowed:
        for c, w in swallowed[:5]:
            print(f'FAIL  A dead-air cut at {clock(c["start"])} would remove the word '
                  f'"{w["word"].strip()}".', file=sys.stderr)
        print("      Refusing to cut speech as silence. Nothing was changed.", file=sys.stderr)
        return 1

    cuts = merge(cuts)
    bad = check_no_word_split(cuts, words)
    if bad:
        for c, w in bad[:5]:
            print(f'FAIL  A cut at {clock(c["start"])} would land inside the word '
                  f'"{w["word"]}".', file=sys.stderr)
        print("      Refusing to cut mid-word. Nothing was changed.", file=sys.stderr)
        return 1

    keeps = to_keeps(cuts, duration)
    removed = sum(c["end"] - c["start"] for c in cuts)
    kept = sum(k[1] - k[0] for k in keeps)

    by_kind = {}
    for c in cuts:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1

    (outdir / "cleanup.json").write_text(json.dumps({
        "duration_in": round(duration, 3),
        "duration_out": round(kept, 3),
        "removed_seconds": round(removed, 3),
        "motion_aware": motion.ok,
        "keep": keeps,
        "cuts": cuts,
        "skipped": notes,
        "quiet_speech": spoken,
    }, indent=1), encoding="utf-8")

    write_notes(outdir / "cleanup-notes.md", cuts, notes, duration, kept, by_kind, spoken)

    print(f"{len(cuts)} {len(keeps)} {round(removed,1)} {round(duration,1)} "
          f"{round(kept,1)} {len(notes)} {len(spoken)}")
    return 0


def write_notes(path, cuts, notes, duration, kept, by_kind, spoken=()):
    lines = ["# Cleanup", "",
             f"**{clock(duration)} in, {clock(kept)} out** — "
             f"{clock(duration - kept)} of dead air, filler and false starts removed.",
             ""]
    if by_kind:
        lines += ["| What | How many |", "|---|---|"]
        for k, v in sorted(by_kind.items()):
            lines.append(f"| {k} | {v} |")
        lines.append("")
    if cuts:
        lines += ["## Every cut", "", "| At | Length | Why |", "|---|---|---|"]
        for c in cuts:
            lines.append(f'| {clock(c["start"])} | {c["end"]-c["start"]:.2f}s '
                         f'| {c["reason"]} |')
        lines.append("")
    if notes:
        lines += ["## Left alone", "",
                  "Each of these was a cut worth making that would have landed while "
                  "the picture was moving. Screen Studio's zooms are baked into the "
                  "export, so a cut inside one jumps.", ""]
        lines += [f"- {n}" for n in notes]
        lines.append("")
    if spoken:
        lines += ["## Quiet speech kept", "",
                  "The level meter read these stretches as silence; the transcript heard "
                  "words in them. Only the gaps around the words were trimmed.", ""]
        lines += [f"- {n}" for n in spoken]
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
