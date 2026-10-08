from server.pipeline import edl, hooks, narrate
from server.pipeline.transcribe import group_segments
from tests.test_edl import W


def _t():
    words = W(("Let", 0.0, 0.2), ("me", 0.2, 0.3), ("explain.", 0.3, 0.6),
              ("What", 1.0, 1.2), ("I", 1.2, 1.3), ("found", 1.3, 1.5), ("is", 1.5, 1.6), ("you", 1.6, 1.7),
              ("need", 1.7, 1.9), ("$1,000", 1.9, 2.4), ("per", 2.4, 2.5), ("ad.", 2.5, 2.8),
              ("Most", 3.0, 3.2), ("people", 3.2, 3.5), ("underspend.", 3.5, 4.0))
    return {"words": words, "segments": group_segments(words), "duration": 5.0}


def test_find_start_matches_mid_sentence_including_symbols():
    t = _t()
    a, b = t["segments"][1]["words"]
    assert hooks.find_start(t["words"], a, b, "you need $1,000 per ad") == 7
    assert hooks.find_start(t["words"], a, b, "not in the sentence") is None


def test_apply_moves_opener_first_and_starts_mid_sentence():
    t = _t()
    clip = {"title": "x", "hook": "Let me explain.", "segment_ids": [0, 1, 2], "score": 5, "reason": ""}
    out = hooks.apply(clip, {"opener_id": 1, "start_at": "You need $1,000", "hook": "You need $1,000 per ad.",
                             "score": 8, "why": "number up front"}, t)
    assert out["segment_ids"] == [1, 0, 2] and out["start_word"] == 7 and out["score"] == 8
    order = edl._plan_inputs(t, {"mode": "clips", "clips": [out]})["clip_0"]
    assert [t["words"][i]["w"].strip() for i in order[:3]] == ["you", "need", "$1,000"]
    assert 4 not in order  # the preamble "What I found is" is gone


def test_apply_ignores_an_opener_outside_the_clip():
    clip = {"title": "x", "hook": "h", "segment_ids": [0, 1], "score": 5, "reason": ""}
    assert hooks.apply(clip, {"opener_id": 2, "start_at": "", "hook": "", "score": 9, "why": ""}, _t()) == clip


def test_polish_reranks_by_new_score(monkeypatch):
    monkeypatch.setattr(narrate, "_call", lambda *a, **k: {"clips": [
        {"clip": 0, "opener_id": 0, "start_at": "", "hook": "Let me explain.", "score": 4, "why": "weak"},
        {"clip": 1, "opener_id": 2, "start_at": "", "hook": "Most people underspend.", "score": 9, "why": "strong"},
    ]})
    clips = [{"title": "a", "hook": "", "segment_ids": [0, 1], "score": 8, "reason": ""},
             {"title": "b", "hook": "", "segment_ids": [1, 2], "score": 6, "reason": ""}]
    out = hooks.polish(_t(), {}, clips, "talking")
    assert [c["title"] for c in out] == ["b", "a"] and out[0]["segment_ids"] == [2, 1]
