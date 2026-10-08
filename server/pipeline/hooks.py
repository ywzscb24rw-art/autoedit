"""A focused second pass on clip openings.

Picking clips and writing cold opens in one request asks too much at once: in practice
Claude picks good clips but keeps their natural (often setup-heavy) first sentence. This
pass looks at each chosen clip on its own and decides how it should open: keep the first
sentence, move the strongest line to the front, and/or start partway into a sentence to
skip a preamble ("what I figured out is...").
"""

import re

from . import narrate

SYSTEM = """You are polishing the opening of short-form video clips (TikTok, Reels, Shorts). A viewer decides in the first two seconds whether to keep watching, and they have no context.

For each clip you get its sentences in playback order, each as `[id] text`. Decide how the clip should open:

- If the current first sentence already works cold (a bold claim, a surprising number, a question, or a problem the viewer has, understandable with no context), keep it: set opener_id to that sentence's id and start_at to "".
- Otherwise open with the strongest line in the clip, such as the claim, the number, the result, or the question it answers. Set opener_id to that sentence. The rest of the clip then plays in its original order without it, so make sure the rest still flows after it.
- If only part of a sentence works as an opener (for example after a preamble like "just from doing this, what I figured out is"), set start_at to the exact words where the clip should begin, copied verbatim from that sentence. Otherwise use "".
- The opener must stand alone. Avoid pronouns or words that point back ("this", "that", "another", "these numbers") unless the opener itself explains them.

Return each clip's new first line as `hook`, a fresh 1-10 score for how likely the clip is to perform with this opening, and a short reason."""

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip": {"type": "integer"},
                    "opener_id": {"type": "integer"},
                    "start_at": {"type": "string"},
                    "hook": {"type": "string"},
                    "score": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["clip", "opener_id", "start_at", "hook", "score", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

_tok = re.compile(r"[a-z0-9$%']+")


def _tokens(text: str) -> list[str]:
    return _tok.findall(text.lower())


def find_start(words: list[dict], a: int, b: int, start_at: str) -> int | None:
    """Index of the word in words[a:b] where `start_at` begins, matching on its first few tokens."""
    want = _tokens(start_at)[:4]
    if not want:
        return None
    seg = [(i, t) for i in range(a, b) for t in _tokens(words[i]["w"])]
    for k in range(len(seg) - len(want) + 1):
        if [t for _, t in seg[k : k + len(want)]] == want:
            return seg[k][0]
    return None


def apply(clip: dict, decision: dict, transcript: dict) -> dict:
    """The clip with its new opening: opener first, the rest in order, optional mid-sentence start."""
    ids = clip["segment_ids"]
    opener = decision["opener_id"]
    if opener not in ids:
        return clip
    out = {**clip, "segment_ids": [opener, *[i for i in ids if i != opener]],
           "hook": decision["hook"], "score": decision["score"], "hook_why": decision["why"],
           "original_hook": clip["hook"], "start_word": None}
    if decision["start_at"].strip():
        a, b = transcript["segments"][opener]["words"]
        start = find_start(transcript["words"], a, b, decision["start_at"])
        if start is not None and start > a:
            out["start_word"] = start
    return out


def polish(transcript: dict, auto_cuts: dict[str, str], clips: list[dict], content: str) -> list[dict]:
    if not clips:
        return clips
    words, segs = transcript["words"], transcript["segments"]

    def text(sid: int) -> str:
        a, b = segs[sid]["words"]
        return "".join(words[i]["w"] for i in range(a, b) if str(i) not in auto_cuts).strip()

    body = "\n\n".join(
        f"Clip {n}: “{c['title']}”\n" + "\n".join(f"[{sid}] {text(sid)}" for sid in c["segment_ids"])
        for n, c in enumerate(clips)
    )
    result = narrate._call(SYSTEM, narrate._content_line(content) + body, SCHEMA, effort="medium")
    by_clip = {d["clip"]: d for d in result["clips"]}
    polished = [apply(c, by_clip[n], transcript) if n in by_clip else c for n, c in enumerate(clips)]
    polished.sort(key=lambda c: -c["score"])
    return polished
