"""Prepare a recording for editing: a 16 kHz mono WAV for Whisper, and a constant-frame-rate
H.264 MP4 that the browser can play and ffmpeg can cut precisely.

Camera and phone MP4s that are already H.264 with a constant frame rate are only re-wrapped,
which takes under a second and keeps them at full resolution. Everything else (browser webm
recordings, HEVC, variable frame rates) is transcoded on the Mac's hardware encoder.
"""

import functools
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable

MAX_WIDTH = 3840
COMMON_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
              Fraction(50), Fraction(60000, 1001), Fraction(60)]


def run(cmd: list[str], duration: float = 0, on_progress: Callable[[float], None] | None = None) -> None:
    """Run ffmpeg. If on_progress is given, report the fraction of `duration` processed."""
    if on_progress and duration:
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if on_progress and duration:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and line[12:].strip().isdigit():
                on_progress(min(int(line[12:]) / 1e6 / duration, 1.0))
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{err[-2000:]}")


@functools.cache
def has_videotoolbox() -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "h264_videotoolbox" in out


def hw_decode() -> list[str]:
    return ["-hwaccel", "videotoolbox"] if has_videotoolbox() else []


def video_encoder() -> list[str]:
    """Hardware H.264 on Macs (several times faster), libx264 elsewhere."""
    if has_videotoolbox():
        return ["-c:v", "h264_videotoolbox", "-q:v", "65"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def _fps(s: str | None) -> Fraction | None:
    try:
        f = Fraction(s)
        return f if 0 < f <= 240 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def target_fps(r: Fraction | None, avg: Fraction | None) -> Fraction:
    """Choose a constant output rate. Snap to a standard rate when close, otherwise round."""
    f = avg or r or Fraction(30)
    closest = min(COMMON_FPS, key=lambda c: abs(f - c))
    if abs(f - closest) / closest < 0.02:
        return closest
    return Fraction(min(60, max(1, round(f))))


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video"), {})
    a = next((s for s in streams if s["codec_type"] == "audio"), {})
    r, avg = _fps(v.get("r_frame_rate")), _fps(v.get("avg_frame_rate"))
    fps = target_fps(r, avg)
    return {
        "duration": float(info["format"].get("duration") or 0),
        "format": info["format"].get("format_name", ""),
        "has_audio": bool(a),
        "acodec": a.get("codec_name"),
        "vcodec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "width": v.get("width"),
        "height": v.get("height"),
        "cfr": r is not None and r == avg,
        "fps": f"{fps.numerator}/{fps.denominator}",
    }


def _atomic(dest: Path) -> Path:
    """A temp path next to dest. Write there, then rename, so a crash never leaves a half file."""
    return dest.with_name(dest.stem + ".partial" + dest.suffix)


def extract_audio(raw: Path, audio: Path) -> None:
    tmp = _atomic(audio)
    run(["ffmpeg", "-y", "-i", str(raw), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(audio)


def can_remux(info: dict) -> bool:
    return (
        info["vcodec"] == "h264"
        and info["pix_fmt"] == "yuv420p"
        and info["cfr"]
        and "mp4" in info["format"]
        and (info["width"] or 0) <= MAX_WIDTH
    )


def convert_video(raw: Path, source: Path, info: dict, on_progress: Callable[[float], None] | None = None) -> None:
    tmp = _atomic(source)
    audio = ["-c:a", "copy"] if info["acodec"] == "aac" else ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    if can_remux(info):
        cmd = ["ffmpeg", "-y", "-i", str(raw), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", *audio]
    else:
        scale = [] if (info["width"] or 0) <= MAX_WIDTH else ["-vf", f"scale={MAX_WIDTH}:-2"]
        cmd = [
            "ffmpeg", "-y", *hw_decode(), "-i", str(raw), "-map", "0:v:0", "-map", "0:a:0",
            *scale, "-fps_mode", "cfr", "-r", info["fps"], *video_encoder(), *audio,
        ]
    run([*cmd, "-movflags", "+faststart", str(tmp)], info["duration"], on_progress)
    tmp.replace(source)
