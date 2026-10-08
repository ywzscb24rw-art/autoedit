"""Face-tracking reframe for vertical (9:16) clips.

For each rendered range we sample frames, detect faces with Apple's Vision framework, split
the samples into shots at scene cuts, and plan a horizontal crop position per shot:

- If every face in the shot fits in one crop window, the crop holds still (like a locked-off
  camera). This is the common case and looks the most deliberate.
- Otherwise a dead-zone follower pans smoothly toward the subject. It only moves once the face
  drifts away from centre, so there's no jitter.

Without Vision (non-Mac) or when no face is found, the crop falls back to the frame centre.
"""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

SAMPLE_FPS = 6
ANALYSIS_WIDTH = 640
# Scene cuts: a big brightness-histogram change, or a big pixel change with a moderate histogram
# change (whip pans). Handheld shake moves pixels but barely changes the histogram (< 0.1).
CUT_HIST = 0.3
CUT_PIXEL, CUT_PIXEL_HIST = 60, 0.12
DEAD_ZONE = 0.12  # fraction of crop width the subject may drift before the camera moves
FOLLOW = 0.35  # fraction of the remaining distance covered per sample while panning


@dataclass
class Face:
    cx: float  # centre x, 0..1 of frame width
    w: float  # width, 0..1 of frame width
    area: float


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
            (x, _y), (w, h) = obs.boundingBox()
            if obs.confidence() >= min_conf:
                out.append(Face(cx=x + w / 2, w=w, area=w * h))
        return out

    faces = boxes(faces_req, 0.5)
    if faces:
        return faces
    # A person box is much bigger than a face; scale its area down so the same
    # "group" and "largest" rules in subject_x still compare sensibly.
    return [Face(cx=f.cx, w=f.w * 0.4, area=f.area * 0.1) for f in boxes(people_req, 0.6)]


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


def split_shots(thumbs: list[bytes]) -> list[int]:
    """Indices where a new shot starts, from raw grayscale thumbnails of equal size."""
    starts = [0]
    for i in range(1, len(thumbs)):
        a, b = thumbs[i - 1], thumbs[i]
        pixel = sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))
        hist = sum(abs(x - y) for x, y in zip(_hist(a), _hist(b))) / 2
        if hist > CUT_HIST or (pixel > CUT_PIXEL and hist > CUT_PIXEL_HIST):
            starts.append(i)
    return starts


def plan_shot(targets: list[float | None], crop_w: float) -> list[float]:
    """Crop centre (0..1) for each sample in one shot."""
    known = [t for t in targets if t is not None]
    if not known:
        return [0.5] * len(targets)
    # Fill gaps (missed detections) from the nearest earlier sample, else the first known one.
    filled, last = [], known[0]
    for t in targets:
        last = t if t is not None else last
        filled.append(last)
    lo, hi = min(filled), max(filled)
    if hi - lo <= crop_w * 0.5:
        return [(lo + hi) / 2] * len(filled)  # locked-off shot
    pos, path = filled[0], []
    for t in filled:
        if abs(t - pos) > crop_w * DEAD_ZONE:
            pos += (t - pos) * FOLLOW
        path.append(pos)
    return path


def plan(targets: list[float | None], shot_starts: list[int], crop_w: float) -> list[float]:
    bounds = [*shot_starts, len(targets)]
    path: list[float] = []
    for a, b in zip(bounds, bounds[1:]):
        path.extend(plan_shot(targets[a:b], crop_w))
    return path


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
        "ffmpeg", "-y", *hw_decode(), "-ss", f"{start:.6f}", "-t", f"{dur:.6f}", "-i", str(source),
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


def keyframes(path: list[float], shot_starts: list[int], width: int, crop_px: int, steps: int = 4) -> list[tuple[float, int]]:
    """Turn per-sample crop centres into ffmpeg keyframes. Pans are interpolated `steps` times
    per sample so motion is smooth; cuts between shots jump instantly."""
    def left(cx: float) -> int:
        return int(min(max(cx * width - crop_px / 2, 0), width - crop_px)) // 2 * 2

    cuts = set(shot_starts)
    keys: list[tuple[float, int]] = []
    for i, cx in enumerate(path):
        t = i / SAMPLE_FPS
        nxt = path[i + 1] if i + 1 < len(path) and (i + 1) not in cuts else None
        for j in range(steps if nxt is not None else 1):
            x = left(cx + (nxt - cx) * j / steps) if nxt is not None else left(cx)
            if not keys or keys[-1][1] != x:  # only emit changes; a locked-off shot is one keyframe
                keys.append((t + j / (SAMPLE_FPS * steps), x))
    return keys or [(0.0, left(0.5))]


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
    shots = split_shots(thumbs)
    return keyframes(plan(targets, shots, crop_w), shots, width, crop_px)


def crop_filter(keys: list[tuple[float, int]], width: int, height: int, tmp: Path) -> str:
    """ffmpeg filter that crops to 9:16 following the keyframes, then scales to 1080x1920."""
    crop_px = crop_width(width, height)
    cmds = tmp / "crop.cmd"
    cmds.write_text("".join(f"{t:.3f} crop x {x};\n" for t, x in keys[1:]))
    head = f"sendcmd=f='{cmds}'," if len(keys) > 1 else ""
    return f"{head}crop={crop_px}:{height}:{keys[0][1]}:0,scale=1080:1920"
