"""Normalize any recording into a constant-frame-rate MP4 plus a 16 kHz mono WAV for Whisper."""

import json
import subprocess
from pathlib import Path

FPS = 30


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{proc.stderr[-2000:]}")


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s for s in streams if s["codec_type"] == "video"), None)
    return {
        "duration": float(info["format"].get("duration") or 0),
        "has_audio": any(s["codec_type"] == "audio" for s in streams),
        "width": video and video.get("width"),
        "height": video and video.get("height"),
    }


def ingest(raw: Path, source: Path, audio: Path) -> dict:
    info = probe(raw)
    if not info["has_audio"]:
        raise RuntimeError("Recording has no audio track. Enable the microphone when recording.")

    # MediaRecorder webm has variable frame rate and poor seek indexes; re-encode to CFR H.264.
    # Cap width at 1920 and force even dimensions for yuv420p.
    run([
        "ffmpeg", "-y", "-i", str(raw),
        "-vf", f"scale='min(1920,iw)':-2,fps={FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(source),
    ])
    run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)])
    return probe(source)
