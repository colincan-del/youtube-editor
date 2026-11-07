# Default brand

Neutral placeholder so the pipeline runs before a real brand exists.

| File | Needed? | What it does |
|---|---|---|
| `brand.json` | yes | Colours, gradient, fonts, caption position, a transcription hint |
| `glossary.md` | yes | Words WhisperX mishears; also voice rules and banned words |
| `fonts/` | yes | The fonts named in brand.json. Get new ones with `scripts/fetch-fonts.py <brand>` |
| `icp.md` | no | If present, must have "Published on the site — cleared for use" and "Held back" sections; stage 5 flags any held-back name spoken on camera |
| `thumbnail-refs/` | no | Screenshots of thumbnails you like |
