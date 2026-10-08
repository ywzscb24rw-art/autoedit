"""Detect captions already burned into the footage, so we don't stack ours on top of them.

Sample frames where someone is talking, read the on-screen text with Apple Vision, and count
a frame as captioned only if a text line in the lower part of the frame matches the words
being spoken around that moment. Whiteboards, signs and slide titles fail at least one of
those tests: they sit elsewhere, don't match the speech, or don't change from frame to frame.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .ingest import hw_decode

SAMPLES = 12
WINDOW = 2.5  # seconds of speech either side of a sample to match against
LOWER = 0.45  # caption lines sit in the lower 45% of the frame
MIN_MATCH = 2  # spoken words a line must share to count as a caption
SHARE = 0.4  # fraction of sampled frames that must be captioned

_word = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return {t for t in _word.findall(text.lower()) if len(t) >= 3}


def read_text(jpeg: Path) -> list[tuple[str, float]]:
    """Recognised text lines in a frame: (text, centre y from the top, 0..1)."""
    import Vision
    from Foundation import NSData

    data = jpeg.read_bytes()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(data, len(data)), None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(1)  # fast
    req.setUsesLanguageCorrection_(False)
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return []
    lines = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if cand:
            (_x, y), (_w, h) = obs.boundingBox()
            lines.append((str(cand[0].string()), 1 - (y + h / 2)))
    return lines


def caption_matches(lines: list[tuple[str, float]], spoken: set[str]) -> frozenset[str] | None:
    """The spoken words found in a lower-frame text line, or None if no line looks like a caption."""
    best: set[str] = set()
    for text, y in lines:
        if y < 1 - LOWER:
            continue
        hit = _tokens(text) & spoken
        if len(hit) > len(best):
            best = hit
    return frozenset(best) if len(best) >= MIN_MATCH else None


def decide(matches: list[frozenset[str] | None]) -> bool:
    """Captioned if enough frames match speech and the matched text varies between frames."""
    hits = [m for m in matches if m]
    return len(hits) >= max(3, SHARE * len(matches)) and len(set(hits)) >= max(2, len(hits) // 2)


def has_burned_captions(source: Path, transcript: dict) -> bool:
    try:
        import Vision  # noqa: F401
    except ImportError:
        return False
    words = transcript["words"]
    if len(words) < 20:
        return False
    picks = [words[round((i + 0.5) * len(words) / SAMPLES)] for i in range(SAMPLES)]
    tmp = Path(tempfile.mkdtemp(prefix="autoedit-ocr-"))
    try:
        matches = []
        for n, w in enumerate(picks):
            t = (w["start"] + w["end"]) / 2
            spoken = _tokens(" ".join(x["w"] for x in words if t - WINDOW <= x["start"] <= t + WINDOW))
            f = tmp / f"{n}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", *hw_decode(source), "-ss", f"{t:.3f}", "-i", str(source),
                            "-frames:v", "1", "-vf", "scale=1280:-2", str(f)], check=True)
            matches.append(caption_matches(read_text(f), spoken) if f.exists() else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return decide(matches)
