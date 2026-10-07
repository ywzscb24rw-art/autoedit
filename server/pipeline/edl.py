"""Edit decision list: turn per-word keep/cut decisions plus a segment order into time ranges."""

from dataclasses import dataclass


@dataclass
class EdlParams:
    max_gap: float = 0.6  # pauses longer than this are compressed
    pad_before: float = 0.08  # Whisper word starts run slightly late
    pad_after: float = 0.12  # and word ends run slightly early
    merge_within: float = 0.05


def word_cut_reasons(transcript: dict, edits: dict) -> dict[int, str]:
    """Final reason for every cut word after applying the user's overrides. Kept words are absent."""
    words, segments = transcript["words"], transcript["segments"]
    reasons: dict[int, str] = {int(k): v for k, v in edits.get("auto_cuts", {}).items()}
    for sid in edits.get("segment_cuts", {}):
        a, b = segments[int(sid)]["words"]
        for i in range(a, b):
            reasons[i] = "ai"
    for k, keep in edits.get("overrides", {}).items():
        if keep:
            reasons.pop(int(k), None)
        else:
            reasons[int(k)] = "manual"
    return {i: r for i, r in reasons.items() if 0 <= i < len(words)}


def ordered_kept_words(transcript: dict, segment_order: list[int], cut: dict[int, str]) -> list[int]:
    out = []
    for sid in segment_order:
        a, b = transcript["segments"][sid]["words"]
        out.extend(i for i in range(a, b) if i not in cut)
    return out


def build_ranges(words: list[dict], order: list[int], duration: float, p: EdlParams = EdlParams()) -> list[list[float]]:
    """Chain consecutive kept words into ranges, pad them, and merge ranges that touch.

    A chain breaks when the next kept word is not the next source word (something was
    cut between them or the order jumps) or when the pause before it exceeds max_gap.
    """
    if not order:
        return []
    chains: list[list[int]] = [[order[0], order[0]]]
    for i in order[1:]:
        a, b = chains[-1]
        if i == b + 1 and words[i]["start"] - words[b]["end"] <= p.max_gap:
            chains[-1][1] = i
        else:
            chains.append([i, i])

    ranges: list[list[float]] = []
    for a, b in chains:
        s = words[a]["start"] - p.pad_before
        e = words[b]["end"] + p.pad_after
        # Padding must not bleed into the neighbouring source words (e.g. a cut "um").
        if a > 0:
            s = max(s, min(words[a - 1]["end"], words[a]["start"]))
        if b + 1 < len(words):
            e = min(e, max(words[b + 1]["start"], words[b]["end"]))
        s, e = max(0.0, s), min(duration, e) if duration else e
        if e - s > 0.03:
            ranges.append([round(s, 3), round(e, 3)])

    merged = [ranges[0]] if ranges else []
    for s, e in ranges[1:]:
        ls, le = merged[-1]
        if ls <= s <= le + p.merge_within:
            merged[-1][1] = max(le, e)
        else:
            merged.append([s, e])
    return merged


def total(ranges: list[list[float]]) -> float:
    return round(sum(e - s for s, e in ranges), 3)


def compute(transcript: dict, edits: dict, p: EdlParams = EdlParams()) -> dict:
    """Return {"main": ranges} for clean mode, or {"clip_<n>": ranges, ...} for clips mode."""
    cut = word_cut_reasons(transcript, edits)
    words, dur = transcript["words"], transcript["duration"]
    if edits.get("mode") == "clips":
        # Within a clip, the LLM's segment choice is the only segment-level cut.
        clip_cut = {i: r for i, r in cut.items() if r != "ai"}
        return {
            f"clip_{n}": build_ranges(words, ordered_kept_words(transcript, c["segment_ids"], clip_cut), dur, p)
            for n, c in enumerate(edits.get("clips", []))
        }
    order = edits.get("order") or [s["id"] for s in transcript["segments"]]
    return {"main": build_ranges(words, ordered_kept_words(transcript, order, cut), dur, p)}
