# AutoEdit

Record your screen, get it transcribed, and get back an edited video. AutoEdit removes filler words and dead air, then has Claude read the transcript to cut retakes, false starts, and tangents so the result tells a coherent story. It can also pull out standalone short-form clips.

- **Clean edit** (course creators): one tight video with the content intact.
- **Find clips** (clippers): 30–90 s clips, each with a hook and a payoff, and optionally cropped to 9:16.

## Setup

```bash
brew install ffmpeg
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm --prefix web install
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

## Run

```bash
.venv/bin/uvicorn server.main:app --port 8000 --reload --reload-dir server
npm --prefix web run dev     # http://localhost:5173
```

Or run from the command line without the UI:

```bash
.venv/bin/python -m server.cli recording.mp4 --mode clean
.venv/bin/python -m server.cli talk.mp4 --mode clips --vertical
.venv/bin/python -m server.cli recording.mp4 --no-ai   # fillers + silences only
```

## How it works

Each stage writes a file to `data/projects/<id>/`, so a later stage can be re-run without repeating the earlier ones.

| Stage | Code | Output |
|---|---|---|
| Record | `web/src/recorder.ts` | `raw.webm` (screen + mic mixed) |
| Ingest | `server/pipeline/ingest.py` | `source.mp4` (30 fps CFR), `audio.wav` (16 kHz) |
| Transcribe | `server/pipeline/transcribe.py` | `transcript.json`: word timestamps, sentence segments |
| Clean | `server/pipeline/clean.py` | filler / stutter cuts (rule-based) |
| AI edit | `server/pipeline/narrate.py` | Claude's keep/cut/order decisions or clip picks → `edits.json` |
| EDL | `server/pipeline/edl.py` | time ranges → `edl.json` |
| Render | `server/pipeline/render.py` | `outputs/*.mp4` |

Notes:
- Whisper normally drops "um" and "uh". It's primed with a disfluent `initial_prompt` so it transcribes them, which lets us cut them.
- Pauses longer than 0.6 s are compressed. Each kept range is padded so word edges aren't clipped, and padding never extends into a neighbouring cut word.
- Rendering encodes every range as a separate frame-aligned piece and then concatenates them losslessly. This keeps audio and video in sync over hundreds of cuts, and 10 ms fades remove clicks at the cuts.
- Clicking a word in the UI stores an override in `edits.json`. Overrides survive a re-run of the AI edit.

## Tests

```bash
.venv/bin/python -m pytest
```

## Config

- `WHISPER_MODEL`: `small.en` by default. Use `medium.en` for better accuracy or `large-v3` for other languages.
- `CLAUDE_MODEL`: `claude-opus-5-5` by default. `claude-sonnet-5-5` costs half as much and is usually fine for clean edits.
- `AUTOEDIT_DATA`: where projects are stored (default `data/projects`).
