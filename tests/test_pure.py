"""Checks on the pipeline's pure helpers — no video, no WhisperX, no ffmpeg."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cleanup import check_no_word_split, clock, merge, to_keeps  # noqa: E402
from cutlist import held_back_names, mmss, parse_sid, split_icp, subtract  # noqa: E402
from glossary import parse_glossary  # noqa: E402


def cut(start, end, kind="filler", reason="r"):
    return {"start": start, "end": end, "kind": kind, "reason": reason}


def test_clock_and_mmss():
    assert clock(75.25) == "1:15.2"
    assert mmss(-3) == "0:00.0"


def test_merge_joins_overlapping_cuts():
    out = merge([cut(5, 6, "flub"), cut(1, 2), cut(1.5, 3, "dead air")])
    assert [(c["start"], c["end"]) for c in out] == [(1, 3), (5, 6)]
    assert out[0]["kind"] == "filler + dead air"


def test_to_keeps_is_the_complement_of_cuts():
    assert to_keeps([cut(1, 2), cut(5, 6)], 10) == [[0.0, 1], [2, 5], [6, 10]]


def test_subtract_splits_keeps_around_cuts():
    assert subtract([[0, 10]], [(2, 3), (9.98, 10)]) == [[0, 2], [3, 9.98]]


def test_no_word_split_flags_a_cut_inside_a_word():
    words = [{"start": 1.0, "end": 1.5}, {"start": 2.0, "end": 2.4}]
    assert check_no_word_split([cut(0.9, 1.6)], words) == []
    assert len(check_no_word_split([cut(1.2, 1.8)], words)) == 1


def test_parse_sid():
    assert parse_sid("S03", 5) == 2
    assert parse_sid(" 1 ", 5) == 0
    with pytest.raises(ValueError):
        parse_sid("S09", 5)
    with pytest.raises(ValueError):
        parse_sid("intro", 5)


ICP = """## Proof
### Published on the site — cleared for use
- **Acme Builders** cut 3 weeks.
### Held back — real, but not on the site
- **Globex Construction** pilot, $30–80K.
- **Acme Builders** second phase.
## Voice
"""


def test_icp_split_and_held_back_names():
    cleared, held = split_icp(ICP)
    assert "Acme" in cleared and "Globex" in held and "Voice" not in held
    names = held_back_names(ICP)
    assert "Globex Construction" in names
    assert not any("Acme" in n for n in names)


def test_icp_without_the_split_is_unreadable():
    assert split_icp("## Proof\nnothing here\n") == (None, None)


def test_parse_glossary(tmp_path):
    g = tmp_path / "glossary.md"
    g.write_text(
        "## Corrections\n\n| Heard as | Correct |\n|---|---|\n"
        "| over view · overview | **OverVue** |\n| gant | *(leave as spoken)* |\n\n"
        "## Voice corrections\n\n| Never output | Always |\n|---|---|\n"
        "| tool | platform |\n\n"
        "## Banned outright\n\n**synergy** · **leverage**\n",
        encoding="utf-8",
    )
    corrections, voice, banned, _ = parse_glossary(g)
    assert [(v, r) for v, r, _ in corrections] == [("over view", "OverVue"), ("overview", "OverVue")]
    assert voice == [("tool", "platform")]
    assert banned == ["synergy", "leverage"]


@pytest.mark.parametrize("brand", sorted(p.name for p in (Path(__file__).resolve().parent.parent / "brands").iterdir() if (p / "glossary.md").exists()))
def test_every_brand_glossary_parses(brand):
    root = Path(__file__).resolve().parent.parent
    parse_glossary(root / "brands" / brand / "glossary.md")
    icp = root / "brands" / brand / "icp.md"
    if icp.exists():
        assert split_icp(icp.read_text(encoding="utf-8")) != (None, None), "rule 4: icp.md split unreadable"
