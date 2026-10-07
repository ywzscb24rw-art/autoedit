from server.pipeline import edl, narrate
from server.pipeline.transcribe import group_segments
from tests.test_edl import W


def _transcript():
    words = W(("Hi.", 0, 0.3), ("Run", 1, 1.2), ("it.", 1.2, 1.4), ("Wait.", 2, 2.3), ("Run", 3, 3.2), ("this.", 3.2, 3.5))
    return {"words": words, "segments": group_segments(words), "duration": 4.0}


def test_clean_edit_keeps_cut_segments_in_order_for_restoring(monkeypatch):
    monkeypatch.setattr(narrate, "_call", lambda *a: {
        "keep": [0, 3, 99, 3], "cuts": [{"ids": [1, 2], "reason": "retake"}], "summary": "s",
    })
    t = _transcript()
    out = narrate.clean_edit(t, {})
    assert out["order"] == [0, 1, 2, 3]
    assert out["segment_cuts"] == {"1": "retake", "2": "retake"}
    ranges = edl.compute(t, {**out, "mode": "clean"})["main"]
    assert [round(s) for s, _ in ranges] == [0, 3]


def test_clips_filtered_by_duration(monkeypatch):
    monkeypatch.setattr(narrate, "_call", lambda *a: {"summary": "", "clips": [
        {"title": "ok", "hook": "", "segment_ids": [3], "reason": "", "score": 5},
        {"title": "too short", "hook": "", "segment_ids": [0], "reason": "", "score": 9},
    ]})
    out = narrate.find_clips(_transcript(), {}, min_s=0.8, max_s=1)
    assert [c["title"] for c in out["clips"]] == ["ok"]


def test_llm_transcript_hides_auto_cut_words():
    t = _transcript()
    text = narrate.transcript_for_llm(t, {"3": "filler"})
    assert "[2] 00:02.0 (0.3s) …" in text
