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
| Ingest | `server/pipeline/ingest.py` | `audio.wav` (16 kHz, extracted first), `source.mp4` (re-wrapped if already H.264, otherwise hardware-transcoded to a constant frame rate), and for 4K or sparse-keyframe footage `proxy.mp4` (1080p, a keyframe every second) |
| Transcribe | `server/pipeline/transcribe.py` | `transcript.json`: word timestamps, sentence segments |
| Clean | `server/pipeline/clean.py` | filler / stutter cuts (rule-based) |
| AI edit | `server/pipeline/narrate.py` | Claude's keep/cut/order decisions or clip picks → `edits.json` |
| EDL | `server/pipeline/edl.py` | time ranges → `edl.json` |
| B-roll | `server/pipeline/broll.py` | for vlogs, Claude looks at each proposed non-speech shot and keeps or skips it |
| Burn-in check | `server/pipeline/burnin.py` | detects captions already in the footage (Apple Vision text recognition matched against the speech) and turns ours off by default |
| Captions | `server/pipeline/captions.py` | word-by-word burned-in captions (drawn with Pillow, overlaid by ffmpeg) |
| Reframe | `server/pipeline/reframe.py` | face-tracking 9:16 crop path for vertical clips |
| Render | `server/pipeline/render.py` | `outputs/*.mp4` (1080p preview; full-resolution export on demand) |

Notes:
- Whisper normally drops "um" and "uh". It's primed with a disfluent `initial_prompt` so it transcribes them, which lets us cut them.
- Pauses longer than 0.6 s are compressed. Each kept range is padded so word edges aren't clipped, and padding never extends into a neighbouring cut word.
- Audio is extracted first, so transcription runs while the video converts. Video uses the Mac's VideoToolbox hardware encoder.
- Uploading the same file again reuses the existing project instead of processing a second copy.
- 16:9 previews, scene cuts, B-roll frames and face analysis use the 1080p proxy; 4K export and 9:16 clips use the source. Preview pieces are encoded with x264 ultrafast in parallel (the hardware encoder runs parallel jobs one at a time); exports use the hardware encoder.
- Rendering encodes every range as a separate frame-aligned piece and then concatenates them losslessly. This keeps audio and video in sync over hundreds of cuts, and 10 ms fades remove clicks at the cuts.
- Video type (screen recording, talking head, vlog) sets the defaults. Vlogs keep non-speech footage around the dialogue (reactions, scenery, lead-ins, a beat after the punchline), trimmed to scene cuts. Claude reviews each proposed shot from a couple of frames plus the surrounding lines and drops filler like blur, black frames, or a camera pointed at the ground. Decisions are cached in `broll.json`.
- Talking heads are cut tight (pauses over 0.3s close up), alternate a normal and a ~115% face-framed punch-in at each jump cut so cuts look intentional, and get word-by-word captions. Captions and punch-ins can be toggled per project.
- Vertical clips follow faces (Apple Vision, on-device). The crop locks still when the subject stays put, pans smoothly when they move, resets at scene cuts, and uses a person detector when faces are turned away. Without Vision it falls back to a centre crop.
- Clicking a word in the UI stores an override in `edits.json`. Overrides survive a re-run of the AI edit.

## Tests

```bash
.venv/bin/python -m pytest
```

## Config

- `WHISPER_MODEL`: `large-v3-turbo` by default. `small.en` is faster on machines without a GPU.
- `WHISPER_BACKEND`: `auto` uses MLX on the Apple Silicon GPU and faster-whisper on the CPU elsewhere.
- `CLAUDE_MODEL`: `claude-opus-5-5` by default. `claude-sonnet-5-5` costs half as much and is usually fine for clean edits.
- `AUTOEDIT_DATA`: where projects are stored (default `data/projects`).
