"""Claude reviews B-roll by looking at it.

The EDL rules propose non-speech footage to keep around the dialogue (reactions, scenery,
lead-ins). Rules can't tell a parachute opening from a phone swinging at the ground, so each
proposed shot is sent to Claude as one or two small frames with the lines spoken around it,
and Claude keeps or skips it. Decisions are cached per shot, so re-renders are free.
"""

import base64
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import narrate
from .ingest import hw_decode

FRAME_WIDTH = 384
BATCH = 30  # shots per request (up to 2 images each, well under the per-request image limit)
CONTEXT_WORDS = 14

SYSTEM = """You are a video editor choosing B-roll. The dialogue has already been edited. Between and around the kept lines are short non-speech stretches of the original footage, each one shot long. For each stretch you see one or two frames from it and the words spoken just before and after it.

Keep a stretch if it adds something:
- A reaction to what was just said (a laugh, a look, a gesture)
- The payoff of what was said, such as the thing being talked about or the action itself
- An establishing or scenery shot that sets the scene for the next line
- A visual joke

Skip it if it's filler:
- The camera pointed at the ground, the sky, or a pocket by accident
- Heavy blur or whip-pan smear, black or near-black frames
- Someone's back or an object blocking most of the frame
- Footage unrelated to the surrounding lines
- Someone clearly speaking a new line that isn't in the transcript (for example, burned-in captions show dialogue that doesn't match the words around it)

When unsure, skip. A tight video beats a padded one. Give a short reason for each decision."""

SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "why": {"type": "string"},
                },
                "required": ["id", "keep", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}

KIND = {
    "lead": "lead-in before the first line",
    "gap": "pause between two lines",
    "tail": "after the last line",
}


def key(piece: dict) -> str:
    return f"{piece['start']:.3f}-{piece['end']:.3f}"


def _context(words: list[dict], idx: int | None, before: bool) -> str:
    if idx is None:
        return "(nothing)"
    span = words[max(0, idx - CONTEXT_WORDS + 1) : idx + 1] if before else words[idx : idx + CONTEXT_WORDS]
    return "".join(w["w"] for w in span).strip()


def _frames(source: Path, piece: dict, tmp: Path, n: int) -> list[str]:
    """Base64 JPEGs at 1/3 and 2/3 of the shot (one frame for very short shots)."""
    s, e = piece["start"], piece["end"]
    times = [s + (e - s) / 2] if e - s < 1.0 else [s + (e - s) / 3, s + 2 * (e - s) / 3]
    out = []
    for j, t in enumerate(times):
        f = tmp / f"p{n:04d}_{j}.jpg"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *hw_decode(), "-ss", f"{t:.3f}", "-i", str(source),
             "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "5", str(f)],
            check=True,
        )
        out.append(base64.b64encode(f.read_bytes()).decode())
    return out


def _labels(edits: dict) -> dict[str, str]:
    labels = {"main": "the edited video"}
    for n, c in enumerate(edits.get("clips", [])):
        labels[f"clip_{n}"] = f"clip “{c['title']}”"
    return labels


def _review_batch(batch: list[tuple[str, dict, list[str]]], transcript: dict, edits: dict) -> dict[str, dict]:
    words, labels = transcript["words"], _labels(edits)
    content = edits.get("opts", {}).get("content", "vlog")
    blocks: list[dict] = [{"type": "text", "text": narrate._content_line(content) + f"{len(batch)} stretches to review."}]
    for pid, piece, frames in batch:
        blocks.append({"type": "text", "text": (
            f"\n[{pid}] {labels.get(piece['output'], piece['output'])} · {KIND[piece['kind']]} · "
            f"{piece['end'] - piece['start']:.1f}s\n"
            f"  before: “{_context(words, piece['prev_word'], before=True)}”\n"
            f"  after: “{_context(words, piece['next_word'], before=False)}”"
        )})
        for b64 in frames:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    result = narrate._call(SYSTEM, blocks, SCHEMA, effort="medium")
    by_id = {d["id"].strip("[]"): d for d in result["decisions"]}
    # A shot Claude didn't answer for is skipped, in line with "when unsure, skip".
    return {key(piece): {"keep": bool(by_id.get(pid, {}).get("keep", False)), "why": by_id.get(pid, {}).get("why", "no decision")}
            for pid, piece, _ in batch}


def review(source: Path, transcript: dict, edits: dict, pieces: list[dict], cache: dict) -> dict:
    """Return cache updated with a {keep, why} decision for every piece."""
    todo = [pc for pc in pieces if key(pc) not in cache]
    if not todo:
        return cache
    tmp = Path(tempfile.mkdtemp(prefix="autoedit-broll-"))
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            frames = list(pool.map(lambda a: _frames(source, a[1], tmp, a[0]), enumerate(todo)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    items = [(f"b{n}", pc, fr) for n, (pc, fr) in enumerate(zip(todo, frames))]
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for decided in pool.map(lambda b: _review_batch(b, transcript, edits), batches):
            cache.update(decided)
    return cache
