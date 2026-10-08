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

from . import captions as cap
from . import reframe
from .ingest import hw_decode, probe, run, video_encoder

FADE = 0.01  # 10 ms audio fades hide the click at each cut
PREVIEW_HEIGHT = 720  # 16:9 previews render at 720p; full resolution is an explicit export
PUNCH_LEVELS = (1.0, 1.15)  # alternate framings at jump cuts in talking-head edits
PUNCH_MIN_PIECE = 0.8  # pieces shorter than this keep the previous framing (no flicker)


def snap(t: float, fps: Fraction) -> float:
    return float(round(Fraction(t) * fps) / fps)


def timescale(fps: Fraction) -> str:
    """A track timescale in which every frame is a whole number of ticks (24 fps -> 24000)."""
    return str(fps.numerator * (1000 if fps.denominator == 1 else 1))


def output_size(info: dict, vertical: bool, max_height: int | None) -> tuple[int, int]:
    if vertical:
        return 1080, 1920
    w, h = info["width"], info["height"]
    if max_height and h > max_height:
        return round(w * max_height / h / 2) * 2, max_height
    return w, h


def punch_plan(durations: list[float]) -> list[float]:
    """Zoom level per piece: toggle at every jump cut, except into a very short piece."""
    zooms, cur = [], 0
    for k, d in enumerate(durations):
        if k and d >= PUNCH_MIN_PIECE:
            cur ^= 1
        zooms.append(PUNCH_LEVELS[cur])
    return zooms


def _video_args(source: Path, analysis: Path, info: dict, s: float, e: float, vertical: bool, size: tuple[int, int],
                face_track: bool, zoom: float, gpu_ok: bool, tmp: Path) -> tuple[list[str], str | None]:
    """(decoder options, video filter chain) for one piece. Faces are found in `analysis`
    (the light proxy when there is one); positions are relative, so they apply to `source`."""
    w, h = info["width"], info["height"]
    face = reframe.face_at(analysis, (s + e) / 2) if zoom > 1 else None
    if vertical:
        keys = reframe.track(analysis, w, h, s, e - s) if face_track else reframe.centre(w, h)
        return hw_decode(source), reframe.crop_filter(keys, w, h, tmp, zoom, face)
    ow, oh = size
    if zoom > 1:
        cw, ch, x, y = reframe.zoom_box(w, h, ow, oh, zoom, face)
        return hw_decode(source), f"crop={cw}:{ch}:{x}:{y},scale={ow}:{oh}"
    if (ow, oh) == (w, h):
        return hw_decode(source), None
    if gpu_ok and hw_decode(source):
        # Decode and scale on the GPU, then hand the small frames to the encoder.
        return (["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"],
                f"scale_vt=w={ow}:h={oh},hwdownload,format=nv12")
    return hw_decode(source), f"scale={ow}:{oh}"


def _piece(source: Path, s: float, e: float, out: Path, fps: Fraction, decode: list[str],
           vf: str | None, caption_track: Path | None, size: tuple[int, int], preview: bool) -> None:
    d = e - s
    inputs = [*decode, "-ss", f"{s:.6f}", "-t", f"{d:.6f}", "-i", str(source)]
    if caption_track:
        _, strip_h, top = cap.layout(*size)
        inputs += ["-f", "concat", "-safe", "0", "-i", str(caption_track)]
        video = ["-filter_complex", f"[0:v]{vf or 'null'}[v];[v][1:v]overlay=0:{top}:eof_action=pass[out]",
                 "-map", "[out]", "-map", "0:a"]
    else:
        video = ["-vf", vf] if vf else []
    run([
        "ffmpeg", "-y", *inputs, *video,
        "-af", f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, d - FADE):.6f}:d={FADE}",
        *video_encoder(preview),
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        "-t", f"{d:.6f}", "-video_track_timescale", timescale(fps),
        str(out),
    ])


def render(source: Path, ranges: list[list[float]], out: Path, vertical: bool = False,
           max_height: int | None = PREVIEW_HEIGHT, face_track: bool = True,
           punch_in: bool = False, caption_words: list[dict] | None = None,
           analysis_source: Path | None = None) -> Path:
    """max_height=None renders at the source's full resolution (export).
    Vertical renders follow faces unless face_track is False (then they crop the centre).
    punch_in alternates framings at jump cuts; caption_words (kept words, source times)
    burns in captions. analysis_source (a proxy of `source`) is used for face detection."""
    info = probe(source)
    fps = Fraction(info["fps"])
    snapped = []
    for s, e in ranges:
        s, e = snap(s, fps), snap(e, fps)
        if e - s >= 1 / fps:
            snapped.append((s, e))
    if not snapped:
        raise RuntimeError("Nothing left to render. Every word was cut.")

    size = output_size(info, vertical, max_height)
    zooms = punch_plan([e - s for s, e in snapped]) if punch_in else [1.0] * len(snapped)
    # Mixing GPU- and CPU-processed pieces could give the joined file inconsistent stream
    # parameters, so the GPU-only path is used only when no piece needs CPU filters.
    gpu_ok = not punch_in and not caption_words
    preview = max_height is not None

    tmp = Path(tempfile.mkdtemp(prefix="autoedit-"))
    try:
        pieces = [tmp / f"p{n:05d}.mov" for n in range(len(snapped))]
        # CPU-encoded previews scale with cores; the hardware encoder runs one job at a time anyway.
        with ThreadPoolExecutor(max_workers=6 if preview else 2) as pool:
            def one(n: int) -> None:
                (s, e), out_piece = snapped[n], pieces[n]
                work = tmp / f"w{n:05d}"
                work.mkdir()
                decode, vf = _video_args(source, analysis_source or source, info, s, e, vertical, size,
                                         face_track, zooms[n], gpu_ok, work)
                track = cap.track(caption_words, s, e, *size, work) if caption_words else None
                _piece(source, s, e, out_piece, fps, decode, vf, track, size, preview)

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
