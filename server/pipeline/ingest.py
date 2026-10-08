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


SPARSE_KEYFRAMES = 4.0  # seconds; beyond this, seeking with the hardware decoder gets slow


@functools.cache
def max_keyframe_gap(path: Path) -> float:
    """Longest stretch between keyframes, from packet flags (no decoding, ~1s for an hour)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout
    keys = sorted(float(t) for t, _, f in (ln.partition(",") for ln in out.splitlines()) if "K" in f and t not in ("", "N/A"))
    if len(keys) < 2:
        return float("inf")
    return max(b - a for a, b in zip(keys, keys[1:]))


def hw_decode(source: Path | None = None) -> list[str]:
    """Hardware decoding options for seeking into `source`.

    Some files (often re-encoded downloads) have keyframes minutes apart. Seeking into them
    means decoding everything since the last keyframe, which the software decoder does about
    7x faster than VideoToolbox, so those files decode in software."""
    if not has_videotoolbox():
        return []
    if source is not None and max_keyframe_gap(Path(source)) > SPARSE_KEYFRAMES:
        return []
    return ["-hwaccel", "videotoolbox"]


def video_encoder(kind: str = "master") -> list[str]:
    """H.264 encoder options by purpose.

    preview: x264 ultrafast. The Mac's hardware encoder is quick for one stream but runs
      parallel jobs one at a time; x264 renders several pieces at once ~4x faster overall.
    clip: x264 veryfast, for the 9:16 clips that get posted. Same speed (decoding the source
      dominates), same quality (SSIM 0.9939 vs 0.9940), about 1/3 the file size.
    master: the hardware encoder (libx264 off-Mac), for conversions, proxies and exports.
    """
    if kind == "preview":
        return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p"]
    if kind == "clip":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
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


PROXY_HEIGHT = 1080


def needs_proxy(source: Path) -> bool:
    """Previews of footage over 1080p, or with sparse keyframes, render much faster from a proxy."""
    return probe(source)["height"] > PROXY_HEIGHT or max_keyframe_gap(source) > SPARSE_KEYFRAMES


def make_proxy(source: Path, proxy: Path, on_progress: Callable[[float], None] | None = None) -> None:
    """A 1080p copy with a keyframe every second: cheap to decode and to seek into, for previews
    and analysis. Exports still render from the full-quality source."""
    info = probe(source)
    h = min(PROXY_HEIGHT, info["height"])
    w = round(info["width"] * h / info["height"] / 2) * 2
    gop = max(1, round(Fraction(info["fps"])))
    tmp = _atomic(proxy)
    run([
        "ffmpeg", "-y", *hw_decode(source), "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
        "-vf", f"scale={w}:{h}", *video_encoder(), "-g", str(gop), "-c:a", "copy",
        "-movflags", "+faststart", str(tmp),
    ], info["duration"], on_progress)
    tmp.replace(proxy)
