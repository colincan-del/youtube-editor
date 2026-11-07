---
description: Take the recording in raw/ through the whole edit, stopping at each checkpoint
argument-hint: "[project] [--brand name] [--length 12-18|full]"
---

Edit the video in `raw/` (or the project named in $ARGUMENTS). Read `CLAUDE.md` first.

Run the stages in order — `./scripts/preflight.sh`, then `./scripts/01-copy.sh` (pass
`--brand <name>` and `--length <min-max|full>` if given) through `./scripts/11-deliver.sh`.
Every stage resumes: one that is already done says so and exits, so always start from the top.

A video in numbered parts (`name-part1.mp4`, `name-part2.mp4` …, one `name.md`) is one
project, `name`; stage 1 joins the parts. `work/<project>/parts.txt` says where each
starts — on a long video, show Colin the checkpoint 1 cut list one part at a time.

When a stage stops for a proposal, read the brief it names and write the proposal file
yourself — the format is in the docstring of the matching script:

| Stage | Read | Write | Format in |
|---|---|---|---|
| 5 | `sentences.md` + `outline.md` | `cutlist.proposal.json` | `scripts/cutlist.py` |
| 6 | `chapters-outline.md` | `chapters.proposal.json` | `scripts/chapters.py` |
| 7 | `chapters.md` + transcript | `graphics.plan.json` | `scripts/graphics.py` |
| 10 | `package-brief.md` | `package.proposal.json` | `scripts/package.py` |

Write generated copy in the brand's voice — its `voice.md` / `icp.md` if it has them. A
rule failure means rewrite the proposal, never edit around the check.

**Stop at checkpoints 1, 2 and 3** and show Colin what he's approving — short, plain,
a table for the cut list. Only run `--approve` after he answers. Never publish anything.

Graphics render at roughly 1–2× real time: tell Colin before stage 8 on a long video.
