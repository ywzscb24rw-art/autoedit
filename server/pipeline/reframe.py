"""Face-tracking reframe for vertical (9:16) clips.

For each rendered range we sample frames, detect faces with Apple's Vision framework, split
the samples into shots at hard scene cuts, and frame each shot the way a camera operator would:

- Hold still while the subject stays inside a safe zone. Most shots are a single hold.
- When the subject really leaves that zone, make one deliberate move to a new hold, eased in and
  out, slower for longer moves. The camera never hunts back and forth after small head motions.
- Glitches (a missed detection, a stray face for one sample) are filtered out first, and a shot
  with no visible face keeps the previous framing instead of snapping to the centre.

Without Vision (non-Mac) or when no face is found at all, the crop falls back to the centre.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SAMPLE_FPS = 6
ANALYSIS_WIDTH = 640
# Scene cuts: a big brightness-histogram change, or a big pixel change with a moderate histogram
# change (whip pans). Handheld shake moves pixels but barely changes the histogram (< 0.1).
CUT_HIST = 0.3
CUT_PIXEL, CUT_PIXEL_HIST = 60, 0.12
HOLD_RANGE = 0.45  # how far (fraction of crop width) the subject may wander before reframing
MEDIAN = 5  # samples in the glitch filter (~0.8 s)
MOVE_MIN, MOVE_PER_CROP, MOVE_MAX = 0.6, 1.2, 2.0  # seconds per reframing move, by distance
OUTPUT_FPS = 30  # keyframe rate during a move
MIN_HOLD = 6  # samples (1 s); shorter holds in the middle of a shot become part of a move


@dataclass
class Face:
    cx: float  # centre x, 0..1 of frame width
    w: float  # width, 0..1 of frame width
    area: float
    cy: float = 0.5  # centre y, 0..1 of frame height, from the top


def detect_faces(jpeg: bytes) -> list[Face]:
    """Faces in one frame. If none are visible (subject turned away, back to camera), fall back
    to Vision's person detector, so the crop stays on whoever is in the shot."""
    import Vision
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(jpeg, len(jpeg))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    faces_req = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    people_req = Vision.VNDetectHumanRectanglesRequest.alloc().init()
    people_req.setUpperBodyOnly_(True)
    ok, _ = handler.performRequests_error_([faces_req, people_req], None)
    if not ok:
        return []

    def boxes(req, min_conf):
        out = []
        for obs in req.results() or []:
            (x, y), (w, h) = obs.boundingBox()  # Vision's origin is bottom-left
            if obs.confidence() >= min_conf:
                out.append(Face(cx=x + w / 2, w=w, area=w * h, cy=1 - (y + h / 2)))
        return out

    faces = boxes(faces_req, 0.5)
    if faces:
        return faces
    # A person box is much bigger than a face; scale its area down so the same
    # "group" and "largest" rules in subject_x still compare sensibly.
    # Upper-body boxes: the head sits near the top.
    return [Face(cx=f.cx, w=f.w * 0.4, area=f.area * 0.1, cy=max(0.0, f.cy - 0.25)) for f in boxes(people_req, 0.6)]


def vision_available() -> bool:
    try:
        import Vision  # noqa: F401

        return True
    except ImportError:
        return False


def subject_x(faces: list[Face], crop_w: float) -> float | None:
    """Where the crop should centre for one sample (0..1 of frame width), or None if no face.

    Faces at least 40% the size of the largest count as the subject group. If the group fits
    in the crop, frame all of them; otherwise follow the largest (usually closest) face.
    """
    if not faces:
        return None
    largest = max(faces, key=lambda f: f.area)
    group = [f for f in faces if f.area >= 0.4 * largest.area]
    left = min(f.cx - f.w / 2 for f in group)
    right = max(f.cx + f.w / 2 for f in group)
    if right - left <= crop_w * 0.9:
        return (left + right) / 2
    return largest.cx


def _hist(thumb: bytes, bins: int = 16) -> list[float]:
    h = [0] * bins
    for v in thumb:
        h[v * bins // 256] += 1
    return [x / max(1, len(thumb)) for x in h]


def split_shots(thumbs: list[bytes], strong_only: bool = False) -> list[int]:
    """Indices where a new shot starts, from raw grayscale thumbnails of equal size.

    strong_only ignores whip pans and fast handheld motion, which the reframer should ride
    through rather than treat as a new shot."""
    starts = [0]
    for i in range(1, len(thumbs)):
        a, b = thumbs[i - 1], thumbs[i]
        pixel = sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))
        hist = sum(abs(x - y) for x, y in zip(_hist(a), _hist(b))) / 2
        if hist > CUT_HIST or (not strong_only and pixel > CUT_PIXEL and hist > CUT_PIXEL_HIST):
            starts.append(i)
    return starts


def _median_filter(xs: list[float], k: int = MEDIAN) -> list[float]:
    h = k // 2
    return [sorted(xs[max(0, i - h) : i + h + 1])[len(xs[max(0, i - h) : i + h + 1]) // 2] for i in range(len(xs))]


def holds(targets: list[float | None], crop_w: float, start_x: float | None = None) -> list[tuple[int, int, float]]:
    """Split one shot into holds [(first_sample, end_sample, crop_centre)].

    A hold lasts as long as every subject position in it fits within HOLD_RANGE of the crop.
    With no face in the shot, it holds start_x (the previous shot's framing) or the centre.
    """
    n = len(targets)
    known = [t for t in targets if t is not None]
    if not known:
        return [(0, n, 0.5 if start_x is None else start_x)]
    filled, last = [], known[0]
    for t in targets:  # missed detections hold the last seen position
        last = t if t is not None else last
        filled.append(last)
    filled = _median_filter(filled)
    out, a, lo, hi = [], 0, filled[0], filled[0]
    for i in range(1, n):
        nlo, nhi = min(lo, filled[i]), max(hi, filled[i])
        if nhi - nlo > crop_w * HOLD_RANGE:
            out.append((a, i, (lo + hi) / 2))
            a, lo, hi = i, filled[i], filled[i]
        else:
            lo, hi = nlo, nhi
    out.append((a, n, (lo + hi) / 2))
    return out


def _ease(u: float) -> float:
    return u * u * (3 - 2 * u)  # smoothstep: starts and stops gently


def _long_holds(hs: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Drop brief holds between longer ones: when the subject sweeps across the frame (a fast
    camera swing), that's one move, not a chain of small ones at uneven speeds."""
    if len(hs) <= 2:
        return hs
    return [hs[0], *[h for h in hs[1:-1] if h[1] - h[0] >= MIN_HOLD], hs[-1]]


def camera_path(targets: list[float | None], shot_starts: list[int], crop_w: float) -> list[tuple[float, float]]:
    """Crop centre over time [(seconds, x 0..1)]: holds joined by eased moves; cuts jump."""
    bounds = [*shot_starts, len(targets)]
    keys: list[tuple[float, float]] = []
    prev_x = None
    for a, b in zip(bounds, bounds[1:]):
        hs = _long_holds(holds(targets[a:b], crop_w, prev_x))
        keys.append((a / SAMPLE_FPS, hs[0][2]))  # a cut (or the start) jumps straight to the framing
        for (h0a, h0b, x0), (h1a, h1b, x1) in zip(hs, hs[1:]):
            # The move spans any dropped brief holds between the two, at least long enough
            # for its distance, and never eats more than half of either neighbouring hold.
            gap = (h1a - h0b) / SAMPLE_FPS
            dur = max(gap, min(MOVE_MAX, max(MOVE_MIN, MOVE_PER_CROP * abs(x1 - x0) / crop_w)))
            dur = min(dur, gap + ((h0b - h0a) + (h1b - h1a)) / 2 / SAMPLE_FPS)
            t_mid = (a + (h0b + h1a) / 2) / SAMPLE_FPS
            t0 = max(t_mid - dur / 2, keys[-1][0])
            steps = max(1, round(dur * OUTPUT_FPS))
            for j in range(steps + 1):
                keys.append((t0 + dur * j / steps, x0 + (x1 - x0) * _ease(j / steps)))
        prev_x = hs[-1][2]
    return keys


def keyframes(path: list[tuple[float, float]], width: int, crop_px: int) -> list[tuple[float, int]]:
    """Camera path -> ffmpeg crop keyframes in source pixels, emitting only changes."""
    out: list[tuple[float, int]] = []
    for t, cx in path:
        x = int(min(max(cx * width - crop_px / 2, 0), width - crop_px)) // 2 * 2
        if not out or out[-1][1] != x:
            out.append((round(t, 3), x))
    return out or [(0.0, (width - crop_px) // 4 * 2)]


THUMB = (32, 18)


def _sample(source: Path, start: float, dur: float, tmp: Path, jpegs: bool = True) -> tuple[list[Path], list[bytes]]:
    """JPEG frames for face detection (optional) plus tiny grayscale thumbnails for cut
    detection, in one decoding pass."""
    from .ingest import hw_decode, run

    tw, th = THUMB
    if jpegs:
        graph = f"[0:v]fps={SAMPLE_FPS},split[a][b];[a]scale={ANALYSIS_WIDTH}:-2[jpg];[b]scale={tw}:{th},format=gray[raw]"
        outputs = ["-map", "[jpg]", "-q:v", "4", str(tmp / "f%05d.jpg"), "-map", "[raw]"]
    else:
        graph = f"[0:v]fps={SAMPLE_FPS},scale={tw}:{th},format=gray[raw]"
        outputs = ["-map", "[raw]"]
    run([
        # -t before -i limits the input, so every output stops at the range end.
        "ffmpeg", "-y", *hw_decode(source), "-ss", f"{start:.6f}", "-t", f"{dur:.6f}", "-i", str(source),
        "-filter_complex", graph, *outputs, "-f", "rawvideo", str(tmp / "thumbs.raw"),
    ])
    frames = sorted(tmp.glob("f*.jpg"))
    raw = (tmp / "thumbs.raw").read_bytes()
    n = tw * th
    thumbs = [raw[i : i + n] for i in range(0, len(raw) - n + 1, n)]
    return frames, thumbs[: len(frames)] if jpegs else thumbs


def scene_cuts(source: Path, start: float, dur: float) -> list[float]:
    """Times (seconds, source timeline) of scene cuts within a range."""
    tmp = Path(tempfile.mkdtemp(prefix="autoedit-cuts-"))
    try:
        _, thumbs = _sample(source, start, dur, tmp, jpegs=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [round(start + i / SAMPLE_FPS, 3) for i in split_shots(thumbs)[1:]]


def crop_width(width: int, height: int) -> int:
    return min(width, round(height * 9 / 16 / 2) * 2)


def centre(width: int, height: int) -> list[tuple[float, int]]:
    return [(0.0, (width - crop_width(width, height)) // 4 * 2)]


def track(source: Path, width: int, height: int, start: float, dur: float) -> list[tuple[float, int]]:
    """Keyframes [(seconds from range start, crop left edge in source pixels)] for one range."""
    crop_px = crop_width(width, height)
    crop_w = crop_px / width
    fallback = centre(width, height)
    if not vision_available() or crop_px >= width:
        return fallback
    tmp = Path(tempfile.mkdtemp(prefix="autoedit-faces-"))
    try:
        frames, thumbs = _sample(source, start, dur, tmp)
        if not frames:
            return fallback
        targets = [subject_x(detect_faces(f.read_bytes()), crop_w) for f in frames]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return keyframes(camera_path(targets, split_shots(thumbs, strong_only=True), crop_w), width, crop_px)


def face_at(source: Path, t: float) -> Face | None:
    """The largest face in the frame at time t, or None (also None without Vision)."""
    if not vision_available():
        return None
    from .ingest import hw_decode

    tmp = Path(tempfile.mkdtemp(prefix="autoedit-face-"))
    try:
        f = tmp / "f.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *hw_decode(source), "-ss", f"{t:.3f}", "-i", str(source),
                        "-frames:v", "1", "-vf", f"scale={ANALYSIS_WIDTH}:-2", str(f)], check=True)
        faces = detect_faces(f.read_bytes()) if f.exists() else []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return max(faces, key=lambda x: x.area, default=None)


EYE_LINE = 0.38  # where a punched-in face's centre sits, from the top of the frame


def zoom_box(width: int, height: int, out_w: int, out_h: int, zoom: float, face: Face | None) -> tuple[int, int, int, int]:
    """Crop (w, h, x, y) in source pixels for an out_w:out_h frame at `zoom`, framed on the face."""
    ch = min(height, round(height / zoom / 2) * 2)
    cw = min(width, round(ch * out_w / out_h / 2) * 2)
    cx = (face.cx if face else 0.5) * width
    cy = (face.cy if face else 0.5) * height
    x = int(min(max(cx - cw / 2, 0), width - cw)) // 2 * 2
    y = int(min(max(cy - EYE_LINE * ch, 0), height - ch)) // 2 * 2 if zoom > 1 else (height - ch) // 2
    return cw, ch, x, y


def crop_filter(keys: list[tuple[float, int]], width: int, height: int, tmp: Path,
                zoom: float = 1.0, face: Face | None = None) -> str:
    """ffmpeg filter that crops to 9:16 following the keyframes, then scales to 1080x1920."""
    crop_px = crop_width(width, height)
    # A punch-in narrows the 9:16 window around the same centre, with the face on the eye line.
    cw, ch, _, y = zoom_box(width, height, 1080, 1920, zoom, face)
    shift = (crop_px - cw) // 2

    def x_of(x: int) -> int:
        return min(max(x + shift, 0), width - cw) // 2 * 2

    cmds = tmp / "crop.cmd"
    cmds.write_text("".join(f"{t:.3f} crop x {x_of(x)};\n" for t, x in keys[1:]))
    head = f"sendcmd=f='{cmds}'," if len(keys) > 1 else ""
    return f"{head}crop={cw}:{ch}:{x_of(keys[0][1])}:{y},scale=1080:1920"
