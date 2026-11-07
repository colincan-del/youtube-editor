# YouTube editor

A long-form YouTube editing pipeline run by Claude Code. Input: one Screen Studio
recording plus its outline. Output: a folder ready to publish (edited video, chapters,
captions, thumbnail, title and description, LinkedIn clip) without opening a timeline.

Case study: https://colin-uren-portfolio.vercel.app/youtube-editor.html

## How it runs

`scripts/preflight.sh <video>`, then one stage at a time:

| Stage | What it does |
|---|---|
| 01 copy | copies the recording out of `raw/` (never written to) |
| 02 transcribe | WhisperX, word-level timestamps |
| 03 glossary | fixes the words the transcriber gets wrong (brand names, jargon) |
| 04 cleanup | trims long pauses, flubs and restarts |
| 05 cutlist | proposes cuts on sentence boundaries; sign-off 1 |
| 06 chapters | chapter titles and timestamps |
| 07 sample | short preview render |
| 08 graphics | title cards, callouts, thumbnail (Hyperframes); sign-off 2 |
| 09 assemble | final ffmpeg render |
| 10 package | title, description, captions, LinkedIn clip; sign-off 3 |
| 11 deliver | writes `output/<project>/` |

Every video belongs to a brand (`brands/<name>/`: colours, fonts, glossary, voice rules,
banned words). This public copy ships only the neutral `brands/default/`.

`CLAUDE.md` is the operating manual Claude Code follows.

Note: this is a public snapshot of a private working repo. Client material is left out.
