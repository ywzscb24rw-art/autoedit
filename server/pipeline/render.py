"""Render an EDL by encoding each range separately and then concatenating the pieces losslessly.

We avoid a single trim/concat filter graph because reordered ranges make ffmpeg buffer
decoded frames in memory. Every piece is snapped to whole frames of the source's frame
rate so audio and video lengths match exactly. Otherwise A/V drift would build up across
hundreds of cuts.
"""

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

from . import reframe
from .ingest import has_videotoolbox, hw_decode, probe, run, video_encoder

FADE = 0.01  # 10 ms audio fades hide the click at each cut
PREVIEW_HEIGHT = 1080  # previews of 4K footage render about 3x faster at 1080p


def snap(t: float, fps: Fraction) -> float:
    return float(round(Fraction(t) * fps) / fps)


def timescale(fps: Fraction) -> str:
    """A track timescale in which every frame is a whole number of ticks (24 fps -> 24000)."""
    return str(fps.numerator * (1000 if fps.denominator == 1 else 1))


def _video_args(source: Path, info: dict, s: float, e: float, vertical: bool, max_height: int | None,
                face_track: bool, tmp: Path) -> tuple[list[str], list[str]]:
    """(decoder options, filter options) for one piece."""
    w, h = info["width"], info["height"]
    if vertical:
        keys = reframe.track(source, w, h, s, e - s) if face_track else reframe.centre(w, h)
        return hw_decode(), ["-vf", reframe.crop_filter(keys, w, h, tmp)]
    if max_height and h > max_height:
        sw = round(w * max_height / h / 2) * 2
        if has_videotoolbox():
            # Decode, scale and encode all on the GPU; frames never touch the CPU.
            return (["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"],
                    ["-vf", f"scale_vt=w={sw}:h={max_height}"])
        return [], ["-vf", f"scale={sw}:{max_height}"]
    return hw_decode(), []


def _piece(source: Path, s: float, e: float, out: Path, fps: Fraction, video: tuple[list[str], list[str]]) -> None:
    d = e - s
    decode, filters = video
    run([
        "ffmpeg", "-y", *decode, "-ss", f"{s:.6f}", "-i", str(source), "-t", f"{d:.6f}",
        *filters,
        "-af", f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, d - FADE):.6f}:d={FADE}",
        *video_encoder(),
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        "-video_track_timescale", timescale(fps),
        str(out),
    ])


def render(source: Path, ranges: list[list[float]], out: Path, vertical: bool = False,
           max_height: int | None = PREVIEW_HEIGHT, face_track: bool = True) -> Path:
    """max_height=None renders at the source's full resolution (export).
    Vertical renders follow faces unless face_track is False (then they crop the centre)."""
    info = probe(source)
    fps = Fraction(info["fps"])
    snapped = []
    for s, e in ranges:
        s, e = snap(s, fps), snap(e, fps)
        if e - s >= 1 / fps:
            snapped.append((s, e))
    if not snapped:
        raise RuntimeError("Nothing left to render. Every word was cut.")

    tmp = Path(tempfile.mkdtemp(prefix="autoedit-"))
    try:
        pieces = [tmp / f"p{n:05d}.mov" for n in range(len(snapped))]
        with ThreadPoolExecutor(max_workers=4) as pool:
            def one(n: int) -> None:
                (s, e), out_piece = snapped[n], pieces[n]
                work = tmp / f"w{n:05d}"
                work.mkdir()
                _piece(source, s, e, out_piece, fps, _video_args(source, info, s, e, vertical, max_height, face_track, work))

            list(pool.map(one, range(len(snapped))))
        listing = tmp / "list.txt"
        # An explicit duration per piece makes each join land exactly on a frame boundary,
        # even when a piece's audio runs a few milliseconds long.
        listing.write_text("".join(f"file '{p}'\nduration {e - s:.6f}\n" for p, (s, e) in zip(pieces, snapped)))
        partial = out.with_name(out.stem + ".partial.mp4")
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-video_track_timescale", timescale(fps),
            "-movflags", "+faststart", str(partial),
        ])
        partial.replace(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
