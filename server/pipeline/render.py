"""Render an EDL by encoding each range separately and then concatenating the pieces losslessly.

We avoid a single trim/concat filter graph because reordered ranges make ffmpeg buffer
decoded frames in memory. Every piece is snapped to whole frames so audio and video
lengths match exactly. Otherwise A/V drift would build up across hundreds of cuts.
"""

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .ingest import FPS, run

FADE = 0.01  # 10 ms audio fades hide the click at each cut


def snap(t: float) -> float:
    return round(t * FPS) / FPS


def _piece(source: Path, s: float, e: float, out: Path, vertical: bool) -> None:
    d = e - s
    vf = f"fps={FPS}"
    if vertical:
        vf = "crop='min(iw,ih*9/16)':ih,scale=1080:1920," + vf
    run([
        "ffmpeg", "-y", "-ss", f"{s:.4f}", "-i", str(source), "-t", f"{d:.4f}",
        "-vf", vf,
        "-af", f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, d - FADE):.4f}:d={FADE}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(out),
    ])


def render(source: Path, ranges: list[list[float]], out: Path, vertical: bool = False) -> Path:
    snapped = []
    for s, e in ranges:
        s, e = snap(s), snap(e)
        if e - s >= 1 / FPS:
            snapped.append((s, e))
    if not snapped:
        raise RuntimeError("Nothing left to render. Every word was cut.")

    tmp = Path(tempfile.mkdtemp(prefix="autoedit-"))
    try:
        pieces = [tmp / f"p{n:05d}.mkv" for n in range(len(snapped))]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda a: _piece(source, *a[0], a[1], vertical), zip(snapped, pieces)))
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in pieces))
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
        ])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
