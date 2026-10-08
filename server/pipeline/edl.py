"""Edit decision list: turn per-word keep/cut decisions plus a segment order into time ranges."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EdlParams:
    max_gap: float = 0.6  # pauses longer than this are compressed
    pad_before: float = 0.08  # Whisper word starts run slightly late
    pad_after: float = 0.12  # and word ends run slightly early
    merge_within: float = 0.05
    # B-roll: non-speech footage to keep instead of compressing it (0 = off).
    broll_gap: float = 0.0  # per long pause between consecutive lines
    broll_lead: float = 0.0  # before the first line of an output
    broll_tail: float = 0.0  # after the last line (lets a laugh or reaction land)
    min_shot: float = 0.6  # never keep a sliver shorter than this of a new shot after a scene cut


# Content-type presets. Screen recordings and talking heads have nothing worth seeing in a
# pause; vlogs do (reactions, scenery, B-roll), so pauses are kept rather than compressed.
PRESETS = {
    "screen": {"clean": EdlParams(), "clips": EdlParams(broll_tail=0.6)},
    # Talking heads are cut tight: any pause over 0.3s (breaths, thinking) closes to ~0.15s.
    "talking": {
        "clean": EdlParams(max_gap=0.3, pad_before=0.06, pad_after=0.09),
        "clips": EdlParams(max_gap=0.3, pad_before=0.06, pad_after=0.09, broll_lead=0.2, broll_tail=0.8),
    },
    "vlog": {
        "clean": EdlParams(broll_gap=6.0, broll_lead=1.0, broll_tail=2.0),
        "clips": EdlParams(broll_gap=2.5, broll_lead=1.0, broll_tail=2.0),
    },
}


def params_for(edits: dict) -> EdlParams:
    content = edits.get("opts", {}).get("content", "screen")
    mode = "clips" if edits.get("mode") == "clips" else "clean"
    return PRESETS.get(content, PRESETS["screen"])[mode]


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


def _chains(words: list[dict], order: list[int], p: EdlParams) -> list[list[int]]:
    """Runs of consecutive kept words. A chain breaks when the next kept word is not the next
    source word (something was cut between them or the order jumps) or after a long pause."""
    chains: list[list[int]] = [[order[0], order[0]]]
    for i in order[1:]:
        a, b = chains[-1]
        if i == b + 1 and words[i]["start"] - words[b]["end"] <= p.max_gap:
            chains[-1][1] = i
        else:
            chains.append([i, i])
    return chains


def _speech_range(words: list[dict], a: int, b: int, duration: float, p: EdlParams) -> tuple[float, float]:
    s = words[a]["start"] - p.pad_before
    e = words[b]["end"] + p.pad_after
    # Padding must not bleed into the neighbouring source words (e.g. a cut "um").
    if a > 0:
        s = max(s, min(words[a - 1]["end"], words[a]["start"]))
    if b + 1 < len(words):
        e = min(e, max(words[b + 1]["start"], words[b]["end"]))
    return max(0.0, s), (min(duration, e) if duration else e)


def _broll_spans(words, chains, ranges, duration, p):
    """Non-speech spans next to kept speech that B-roll may fill: (kind, start, end).

    lead: silence before the first line. tail: silence after the last line.
    gap: a long pause between two lines that are consecutive in the source (so the pause
    holds no cut speech).
    """
    spans = []
    if p.broll_lead and chains:
        a = chains[0][0]
        prev_end = words[a - 1]["end"] if a > 0 else 0.0
        if ranges[0][0] > prev_end:
            spans.append(("lead", 0, max(prev_end, ranges[0][0] - p.broll_lead - p.min_shot), ranges[0][0]))
    if p.broll_gap:
        for k in range(len(chains) - 1):
            if chains[k + 1][0] == chains[k][1] + 1 and ranges[k + 1][0] > ranges[k][1]:
                spans.append(("gap", k, ranges[k][1], ranges[k + 1][0]))
    if p.broll_tail and chains:
        b = chains[-1][1]
        next_start = words[b + 1]["start"] if b + 1 < len(words) else (duration or ranges[-1][1])
        if next_start > ranges[-1][1]:
            spans.append(("tail", len(chains) - 1, ranges[-1][1], min(next_start, ranges[-1][1] + p.broll_tail + p.min_shot)))
    return spans


def _keep_in_span(kind: str, s: float, e: float, cuts: list[float], p: EdlParams) -> list[tuple[float, float]]:
    """Which part of a non-speech span to keep, aligned to scene cuts.

    Pauses up to the budget are kept whole. Longer ones keep their head (the reaction right
    after a line) and tail (the shot leading into the next line). An edge that would show only
    a sliver of a new shot is pulled back to the cut.
    """
    inside = [c for c in cuts if s < c < e]
    if kind == "lead":
        start = max(s, e - p.broll_lead)
        nxt = [c for c in inside if start <= c < start + p.min_shot]
        return [(nxt[-1] if nxt else start, e)]
    if kind == "tail":
        end = min(e, s + p.broll_tail)
        prv = [c for c in inside if end - p.min_shot < c <= end]
        return [(s, prv[0] if prv else end)]
    if e - s <= p.broll_gap:
        return [(s, e)]
    head_end, tail_start = s + p.broll_gap / 2, e - p.broll_gap / 2
    prv = [c for c in inside if head_end - p.min_shot < c <= head_end]
    nxt = [c for c in inside if tail_start <= c < tail_start + p.min_shot]
    return [(s, prv[0] if prv else head_end), (nxt[-1] if nxt else tail_start, e)]


def _split_at_cuts(s: float, e: float, cuts: list[float], min_len: float) -> list[tuple[float, float]]:
    """Split a stretch into shots at scene cuts, folding slivers (whip pans) into a neighbour."""
    edges = [s, *[c for c in cuts if s < c < e], e]
    shots: list[list[float]] = []
    for a, b in zip(edges, edges[1:]):
        if shots and b - a < min_len:
            shots[-1][1] = b
        elif shots and shots[-1][1] - shots[-1][0] < min_len:
            shots[-1][1] = b
        else:
            shots.append([a, b])
    return [(a, b) for a, b in shots]


def _assemble(words, order, duration, p, cuts, allow):
    """Ranges for one output, plus every B-roll shot considered: (kind, start, end, prev_word, next_word)."""
    chains = _chains(words, order, p)
    ranges = [_speech_range(words, a, b, duration, p) for a, b in chains]

    pieces: list[tuple[str, float, float, int | None, int | None]] = []
    extra: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for kind, k, s, e in _broll_spans(words, chains, ranges, duration, p):
        prev_w = None if kind == "lead" else chains[k][1]
        next_w = chains[0][0] if kind == "lead" else (chains[k + 1][0] if kind == "gap" else None)
        kept = []
        for a, b in _keep_in_span(kind, s, e, cuts, p):
            for x, y in _split_at_cuts(a, b, cuts, p.min_shot / 2):
                if y - x <= 0.05:
                    continue
                pieces.append((kind, round(x, 3), round(y, 3), prev_w, next_w))
                if allow is None or allow(round(x, 3), round(y, 3)):
                    kept.append((x, y))
        extra[(kind, k)] = kept

    out: list[tuple[float, float]] = list(extra.get(("lead", 0), []))
    for k, r in enumerate(ranges):
        out.append(r)
        out.extend(extra.get(("gap", k), []))
        out.extend(extra.get(("tail", k), []))

    merged: list[list[float]] = []
    for s, e in out:
        if e - s <= 0.03:
            continue
        if merged and merged[-1][0] <= s <= merged[-1][1] + p.merge_within:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [[round(s, 3), round(e, 3)] for s, e in merged], pieces


def build_ranges(words: list[dict], order: list[int], duration: float, p: EdlParams = EdlParams(),
                 cuts: list[float] = (), allow=None) -> list[list[float]]:
    """Chain consecutive kept words into padded ranges, add B-roll, and merge ranges that touch.

    allow(start, end) -> bool can veto individual B-roll shots (see broll.py).
    """
    if not order:
        return []
    return _assemble(words, order, duration, p, list(cuts), allow)[0]


def broll_windows(words: list[dict], order: list[int], duration: float, p: EdlParams) -> list[tuple[float, float]]:
    """Source spans that may be kept as B-roll, which need scene-cut analysis before rendering."""
    if not order or not (p.broll_gap or p.broll_lead or p.broll_tail):
        return []
    chains = _chains(words, order, p)
    ranges = [_speech_range(words, a, b, duration, p) for a, b in chains]
    out = []
    for kind, _k, s, e in _broll_spans(words, chains, ranges, duration, p):
        if kind == "gap" and e - s > p.broll_gap + 2 * p.min_shot:
            # Only the head and tail of a long pause can be kept.
            half = p.broll_gap / 2 + p.min_shot
            out += [(s, s + half), (e - half, e)]
        else:
            out.append((s, e))
    return out


def total(ranges: list[list[float]]) -> float:
    return round(sum(e - s for s, e in ranges), 3)


def _plan_inputs(transcript: dict, edits: dict) -> dict[str, list[int]]:
    """Output name -> ordered kept word indices."""
    cut = word_cut_reasons(transcript, edits)
    if edits.get("mode") == "clips":
        # Within a clip, the LLM's segment choice is the only segment-level cut.
        clip_cut = {i: r for i, r in cut.items() if r != "ai"}
        return {
            f"clip_{n}": ordered_kept_words(transcript, c["segment_ids"], clip_cut)
            for n, c in enumerate(edits.get("clips", []))
        }
    order = edits.get("order") or [s["id"] for s in transcript["segments"]]
    return {"main": ordered_kept_words(transcript, order, cut)}


def kept_words(transcript: dict, edits: dict) -> dict[str, list[dict]]:
    """Output name -> the words that survive the edit (source times), for captions."""
    words = transcript["words"]
    return {name: [words[i] for i in order] for name, order in _plan_inputs(transcript, edits).items()}


def windows(transcript: dict, edits: dict, p: EdlParams | None = None) -> list[tuple[float, float]]:
    p = p or params_for(edits)
    words, dur = transcript["words"], transcript["duration"]
    return [w for order in _plan_inputs(transcript, edits).values() for w in broll_windows(words, order, dur, p)]


def compute(transcript: dict, edits: dict, p: EdlParams | None = None, cuts: list[float] = (), allow=None) -> dict:
    """Return {"main": ranges} for clean mode, or {"clip_<n>": ranges, ...} for clips mode.

    cuts are scene-cut times (seconds) used to align B-roll edges; see windows().
    """
    p = p or params_for(edits)
    words, dur = transcript["words"], transcript["duration"]
    return {name: build_ranges(words, order, dur, p, sorted(cuts), allow)
            for name, order in _plan_inputs(transcript, edits).items()}


def broll_pieces(transcript: dict, edits: dict, cuts: list[float] = ()) -> list[dict]:
    """Every B-roll shot the rules would keep, with the words around it, for review."""
    p = params_for(edits)
    words, dur = transcript["words"], transcript["duration"]
    out = []
    for name, order in _plan_inputs(transcript, edits).items():
        if not order:
            continue
        for kind, s, e, prev_w, next_w in _assemble(words, order, dur, p, sorted(cuts), None)[1]:
            out.append({"output": name, "kind": kind, "start": s, "end": e, "prev_word": prev_w, "next_word": next_w})
    return out

