"""Claude reads the transcript and makes the editorial decisions.

Clean mode decides which sentences to keep and in what order (for course creators).
Clips mode finds standalone short-form clips (for clippers).
"""

import json
import os

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

CLEAN_SYSTEM = """You are an expert video editor working from a timestamped transcript of a recording. The first line of the user message describes what kind of video it is. Filler words ("um", "uh") have already been removed automatically. Your job is the editorial pass: decide which sentences stay so that the final video is tight and coherent.

Each line looks like `[id] mm:ss.s (duration) text`.

Cut:
- Retakes. When the speaker says roughly the same thing more than once, keep only the LAST complete, fluent take and cut the earlier attempts.
- False starts and abandoned sentences that trail off or restart.
- Meta-talk about the recording itself, such as "let me start over", "is this recording?", "I'll edit that out", or "hold on, wrong window".
- Off-topic tangents that don't serve the viewer, and redundant repetition.

Keep everything that carries content. The creator wants their material intact, just tighter. When unsure, keep.

Order: return the kept ids in playback order. Stay chronological unless moving a sentence clearly repairs the narrative (for example, "oh, I should have mentioned earlier..."). Reordering causes visual jumps, so do it rarely.

Give a short reason for each group of cut sentences, and a one-paragraph summary of the edit."""

CLIPS_SYSTEM = """You are an expert short-form video editor (TikTok, Reels, Shorts) working from a timestamped transcript of a long recording. Find the best standalone clips.

Each line looks like `[id] mm:ss.s (duration) text`.

Each clip must:
- Open cold. The first sentence is all a scroller hears before deciding to swipe, and they have zero context. It must make sense on its own and create curiosity: a bold claim, a surprising number, a question, or a problem the viewer has. It must not lean on anything before it, so never open with "so if this…", "and then…", "that's why…", "these numbers…", "as I said…", or a pronoun whose referent the viewer hasn't heard yet. If the clip's strongest line comes later (often the conclusion, the diagnosis, or the result), put it first as a cold open, then play the build-up. The `hook` field is that first sentence, verbatim.
- Make complete sense to someone who has not seen the rest of the recording. There should be no dangling references like "as I said before".
- End on a payoff: the answer, the result, or the punchline. Don't let it trail off.
- Contain no retakes, false starts, or meta-talk. Skip those sentences even inside an otherwise contiguous run.
- Run within the target duration. Sum the durations of the sentences you pick.

Prefer contiguous runs of sentences. Rank clips best first, and score each 1-10 for how likely it is to perform. Judge the opening harshly: a clip whose first sentence needs earlier context scores 5 at most. Return fewer clips rather than weak ones."""


CONTENT = {
    "screen": "Screen recording: a tutorial, course lesson, or walkthrough. The picture is a screen capture.",
    "talking": "Talking-head video: one speaker on camera, addressing the viewer.",
    "vlog": (
        "Vlog: casual footage across many scenes, often several people talking over each other, with "
        "music and background chatter. Footage between lines (reactions, scenery, B-roll) is kept "
        "automatically, so judge the lines and the story they tell."
    ),
}


def _content_line(content: str) -> str:
    return f"Video type: {CONTENT.get(content, CONTENT['screen'])}\n\n"


def _fmt_time(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def transcript_for_llm(transcript: dict, cut_words: dict[str, str]) -> str:
    """One line per segment, with auto-cut fillers stripped so the model reads clean text."""
    words = transcript["words"]
    lines = []
    for seg in transcript["segments"]:
        a, b = seg["words"]
        text = "".join(words[i]["w"] for i in range(a, b) if str(i) not in cut_words).strip()
        dur = seg["end"] - seg["start"]
        lines.append(f"[{seg['id']}] {_fmt_time(seg['start'])} ({dur:.1f}s) {text or '…'}")
    return "\n".join(lines)


CLEAN_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "array", "items": {"type": "integer"}, "description": "Kept segment ids in playback order"},
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "reason": {"type": "string"},
                },
                "required": ["ids", "reason"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["keep", "cuts", "summary"],
    "additionalProperties": False,
}

CLIPS_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "hook": {"type": "string", "description": "The opening line, verbatim"},
                    "segment_ids": {"type": "array", "items": {"type": "integer"}, "description": "In playback order"},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["title", "hook", "segment_ids", "reason", "score"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["clips", "summary"],
    "additionalProperties": False,
}


def _call(system: str, user: str | list[dict], schema: dict, effort: str = "high") -> dict:
    """One structured-output request. `user` may be a list of content blocks (text and images)."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("No Claude credentials: add ANTHROPIC_API_KEY to .env, or turn off the AI edit.")
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to edit this transcript.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Claude's edit response was truncated (max_tokens).")
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def _valid_ids(ids, n: int) -> list[int]:
    seen, out = set(), []
    for i in ids:
        if isinstance(i, int) and 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def clean_edit(transcript: dict, auto_cuts: dict[str, str], content: str = "screen") -> dict:
    segs = transcript["segments"]
    n = len(segs)
    result = _call(CLEAN_SYSTEM, _content_line(content) + transcript_for_llm(transcript, auto_cuts), CLEAN_SCHEMA)

    keep = _valid_ids(result["keep"], n)
    if n and len(keep) < 0.2 * n:
        raise RuntimeError(f"AI edit kept only {len(keep)}/{n} sentences; refusing to apply it.")
    kept = set(keep)
    reason_of = {i: c["reason"] for c in result["cuts"] for i in c["ids"]}
    segment_cuts = {str(i): reason_of.get(i, "cut by AI") for i in range(n) if i not in kept}

    # Keep cut segments in `order` at their chronological position, so restoring a word
    # in the UI puts it back where it was spoken.
    order: list[int] = []
    pending_cut = sorted(int(i) for i in segment_cuts)
    for sid in keep:
        while pending_cut and pending_cut[0] < sid:
            order.append(pending_cut.pop(0))
        order.append(sid)
    order.extend(pending_cut)

    return {"order": order, "segment_cuts": segment_cuts, "summary": result["summary"], "clips": []}


def find_clips(transcript: dict, auto_cuts: dict[str, str], min_s: float = 30, max_s: float = 90, max_clips: int = 5,
               content: str = "screen") -> dict:
    segs = transcript["segments"]
    user = (
        _content_line(content)
        + f"Target clip length: {min_s:.0f}-{max_s:.0f} seconds. Return at most {max_clips} clips.\n\n"
        + transcript_for_llm(transcript, auto_cuts)
    )
    result = _call(CLIPS_SYSTEM, user, CLIPS_SCHEMA)
    clips = []
    for c in result["clips"]:
        ids = _valid_ids(c["segment_ids"], len(segs))
        dur = sum(segs[i]["end"] - segs[i]["start"] for i in ids)
        if not ids or dur < min_s * 0.5 or dur > max_s * 1.5:
            continue
        clips.append({**c, "segment_ids": ids, "approx_duration": round(dur, 1)})
    clips.sort(key=lambda c: -c["score"])
    return {"order": [], "segment_cuts": {}, "summary": result["summary"], "clips": clips[:max_clips]}
