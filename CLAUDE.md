# Video Editing System

You edit Colin's YouTube videos. Input is one Screen Studio recording. Output is a folder
Colin can publish from without opening a timeline.

**Every video belongs to a brand** — `brands/<name>/`, chosen at stage 1 with `--brand`
(default: the name in `brands/active`) and recorded in `work/<project>/brand.txt`. The
brand supplies colours, fonts, the glossary, voice rules, banned words and the held-back
list. `brands/default/` is a neutral placeholder; a full brand (OverVue) exists in the private working repo and is left out of this public copy.
Built for OverVue first; since 2026-09-28 Colin wants it usable for any channel.

**This file is the operating manual.** (The full spec, `plan.md`, stays in the private repo.)

---

## Hard rules — never violate these

1. **`raw/` is read-only.** Copy to `work/` first. Never write to `raw/`, never delete from it,
   never pass a path inside it as an ffmpeg output. Losing the recording is the only
   unrecoverable failure in this system.
2. **Never cut mid-word.** Cuts land on sentence boundaries with buffer either side.
3. **Never cut during picture motion.** Screen Studio's zooms are baked into the export.
   A cut inside a zoom jumps. Nudge to the nearest still moment.
4. **Only cleared proof claims.** If the brand has an `icp.md`, it splits proof into
   "Published on the site — cleared for use" and "Held back". Quote only from the cleared
   list; flag any held-back name spoken on camera. Stages 6, 7 and 10 reject any generated
   copy carrying a held-back name or dollar figure. If that structure cannot be parsed,
   **stop and ask** — never guess. A brand with no `icp.md` has nothing held back.
5. **The brand's banned words never appear** in a title, description, chapter name,
   callout or thumbnail — the "Banned outright" section of its `glossary.md`.
6. **The brand's voice corrections always apply** to that same generated copy — the
   "Voice corrections" table of its `glossary.md` ("never X, always Y"). Never to the
   captions, which stay what Colin actually said.
7. **Brand values come from `brand.json`**, never typed into a template or script.
8. **Captions are burned into the LinkedIn clip only.** The long-form video ships without
   them, and `captions.srt` goes to YouTube, where viewers turn it on and off. (Decided
   2026-10-19 — burned-in captions can't be turned off, double up with YouTube's CC, and
   cover the screen being demonstrated. LinkedIn autoplays muted, so the clip keeps them.)
9. **AI B-roll is allowed; background music is not.** B-roll goes where the screen is not the
   content — the intro and concept talk — never over footage of the product being demonstrated.
   B-roll generation costs money: propose the shots and their cost at checkpoint 2 and
   generate nothing until Colin approves.
10. **Never publish.** The system produces a folder. Colin uploads.
11. **Never overwrite an existing `output/<project>/`** without explicit confirmation.

## Stop at the checkpoints

The system halts at three points and waits for Colin. Do not continue past one on your own
initiative, and do not collapse two into one.

| # | Stop after | Colin approves |
|---|---|---|
| 1 | Cut list generated | The proposed content cuts. Present as a timestamped table with one-line reasons. Default to approved — he skims. |
| 2 | Look sample rendered | 10 seconds with brand styling applied |
| 3 | Package assembled | Everything, before he uploads |

## Pipeline

Each stage writes its state to `work/<project>/` on completion. A failed run **resumes from
the last completed stage** — check what already exists before redoing work.

```
0  preflight   audio, levels, file intact, outline, brand files → fail fast, plain English
1  copy        raw/ → work/<project>/source.mp4, joining name-part1, -part2 … if the
               video comes in parts; records the brand (--brand) and the length
               target (--length 12-18 by default, or "full" for a walkthrough)
2  transcribe  WhisperX, word-level timestamps → transcript.json
3  glossary    apply the brand's glossary.md → transcript.corrected.json
4  cleanup     dead air, filler, flubbed takes → cleaned.mp4 (a preview only)
5  cutlist     *Claude proposes content cuts → cutlist.md       ← CHECKPOINT 1 (--approve)
6  chapters    *Claude names chapters → chapters/NN.mov, sound levelled once for all
7  sample      *Claude writes graphics.plan.json → sample.mp4  ← CHECKPOINT 2 (--approve)
8  graphics    every chapter's overlay → graphics/NN/composite.mov (skips up-to-date ones)
9  assemble    → final.mp4, and clean.mov without graphics
10 package     *Claude writes package.proposal.json → title, description, SRT,
               thumbnail.png, linkedin-clip.mp4
11 deliver     → output/<project>/                              ← CHECKPOINT 3
```

Each script is `./scripts/NN-name.sh <project>`. The starred stages run twice: the first
run writes a brief and stops; Claude writes the proposal file (format in the matching
`scripts/*.py` docstring); the second run checks it against the rules and does the work.
`/edit-video` walks the whole thing.

### Two things that make this survivable at length

- **Work chapter by chapter from stage 6 on.** Never render the whole video to change one
  graphic. Each chapter renders independently and combines at the end.
- **Start a fresh session between stages 5 and 8.** The rough cut must be locked before
  graphics begin; carrying transcript context into the graphics pass wastes the window
  and degrades output.

## Reference files

In `brands/<name>/` — see `brands/default/README.md` for what each file does.

| File | What it governs |
|---|---|
| `brand.json` | Colours, gradient, fonts, caption position, transcription hint — what the scripts read |
| `glossary.md` | Transcript corrections, voice corrections, banned words |
| `fonts/` | Vendored fonts — `scripts/fetch-fonts.py <brand>` after changing them |
| `icp.md` *(optional)* | Audience, cleared vs held-back proof |
| `voice.md`, `brand-kit.md` *(optional)* | Human guidance for writing titles and descriptions |
| `thumbnail-refs/` *(optional)* | Thumbnails Colin likes; read for structure |

**A brand file with a `recheck_after` date is checked by preflight.** A stale brand file
produces confident, finished-looking, wrong-coloured video — the failure mode with no
symptom. It happened once with OverVue. Verify against the brand's own website.

## Logging

Write `logs/run.log` and a copy to `output/<project>/run.log`. Human-readable lines — Colin
must be able to read it without a developer.

Log every run: start and end timestamps · input files and sizes · preflight results ·
**which brand, and which colours and fonts were used** · every stage entered and
completed · every cut with timestamps · every tool call and exit status · every error with
context · runtime per stage.

## Tools

| Job | Tool |
|---|---|
| Transcription | WhisperX — word-level timestamps |
| Cutting, trimming, audio, assembly | ffmpeg |
| Graphics, captions, title cards, thumbnail | Hyperframes |
| Recording (Colin's, upstream) | Screen Studio — already provides zooms and webcam compositing |

**Do not add zoom logic.** Screen Studio has already done it.

### Stock media — images, clips, sound effects, fonts

Sources Colin gave on 2026-10-27, to use when possible or wanted. Same placement as AI
B-roll (rule 9): intro and concept talk only, never over the product being demonstrated.
Propose every pick at checkpoint 2.

| Site | What | Watch out for |
|---|---|---|
| [Pixabay](https://pixabay.com/) | Photos, illustrations, video, GIFs | Free, no credit needed |
| [Pexels](https://www.pexels.com/) | Photos, video; free API | Free, no credit needed |
| [Yarn memes](https://memes.getyarn.io/) | Movie/TV clips by quote | Copyrighted: Content ID can claim it. A second or two, sparingly, flagged at checkpoint 2 |
| [FreeSFX](https://freesfx.co.uk/Default.aspx) | Sound effects | Check licence; may need a credit in the description |
| [Epidemic Sound](https://www.epidemicsound.com/) | Music, sound effects | Paid; channel must be linked. SFX only while music is banned |
| [DaFont](https://www.dafont.com/) | Display fonts | Licence per font: only "100% free" / public domain for a business channel. Add via `brands/<name>/fonts/` + `brand.json`, never into a template |

### Before the recording — scripting

This system starts at the recording. The script and the outline come before it, and Colin
already pays for two tools that do that. When he asks for help scripting, ideating or
outlining a video, point him to these first rather than starting from a blank page:

| Tool | What it's for |
|---|---|
| [Subscribr](https://subscribr.ai/) | Ideas, outlier research, full scripts. Has an API for AI agents. |
| [Poppy AI](https://getpoppy.ai/) | Researching what already works, hooks, scripts in his voice |

The outline that preflight requires in `raw/` can come straight from either one.
