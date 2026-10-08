from server.pipeline import edl
from tests.test_edl import W

VLOG = edl.EdlParams(broll_gap=2.0, broll_lead=1.0, broll_tail=1.5, min_shot=0.5)


def test_off_by_default_compresses_pauses():
    words = W(("one", 1.0, 1.5), ("two", 4.0, 4.5))
    assert len(edl.build_ranges(words, [0, 1], 10.0)) == 2


def test_short_pause_kept_whole():
    words = W(("one", 1.0, 1.5), ("two", 3.0, 3.5))
    p = edl.EdlParams(broll_gap=2.0)
    assert edl.build_ranges(words, [0, 1], 10.0, p) == [[0.92, 3.62]]


def test_long_pause_keeps_head_and_tail():
    words = W(("one", 1.0, 1.5), ("two", 9.0, 9.5))
    p = edl.EdlParams(broll_gap=2.0)
    assert edl.build_ranges(words, [0, 1], 20.0, p) == [[0.92, 2.62], [7.92, 9.62]]


def test_head_does_not_end_on_a_sliver_of_the_next_shot():
    words = W(("one", 1.0, 1.5), ("two", 9.0, 9.5))
    p = edl.EdlParams(broll_gap=2.0, min_shot=0.5)
    # A cut at 2.4s would leave only 0.22s of the new shot in the head, so the head ends at the cut.
    ranges = edl.build_ranges(words, [0, 1], 20.0, p, cuts=[2.4])
    assert ranges[0] == [0.92, 2.4]


def test_tail_starts_at_a_cut_rather_than_mid_shot():
    words = W(("one", 1.0, 1.5), ("two", 9.0, 9.5))
    p = edl.EdlParams(broll_gap=2.0, min_shot=0.5)
    # The tail would start at 7.92, just 0.18s before a cut at 8.1, so it starts at the cut.
    ranges = edl.build_ranges(words, [0, 1], 20.0, p, cuts=[8.1])
    assert ranges[1][0] == 8.1


def test_lead_and_tail_around_a_clip():
    words = W(("before", 0.0, 0.5), ("hook", 5.0, 5.5), ("punchline", 5.6, 6.0), ("after", 12.0, 12.5))
    ranges = edl.build_ranges(words, [1, 2], 20.0, VLOG)
    assert ranges == [[3.92, 7.62]]  # 1s lead-in, the lines, 1.5s to let it land


def test_lead_never_includes_cut_speech():
    words = W(("cut", 4.0, 4.6), ("hook", 5.0, 5.5))
    ranges = edl.build_ranges(words, [1], 20.0, VLOG)
    assert ranges[0][0] >= 4.6


def test_pause_around_cut_speech_is_not_filled():
    # Word 1 was cut (e.g. a retake), so the gap between 0 and 2 holds speech we removed.
    words = W(("keep", 1.0, 1.5), ("retake", 3.0, 3.5), ("keep", 6.0, 6.5))
    p = edl.EdlParams(broll_gap=10.0)
    assert len(edl.build_ranges(words, [0, 2], 20.0, p)) == 2


def test_windows_cover_only_what_might_be_kept():
    words = W(("one", 1.0, 1.5), ("two", 30.0, 30.5))
    p = edl.EdlParams(broll_gap=2.0, min_shot=0.5)
    wins = edl.broll_windows(words, [0, 1], 60.0, p)
    assert len(wins) == 2 and all(e - s <= 1.5 + 1e-9 for s, e in wins)


def test_presets_by_content_type():
    assert edl.params_for({"mode": "clips", "opts": {"content": "vlog"}}).broll_gap > 0
    assert edl.params_for({"mode": "clean", "opts": {"content": "screen"}}).broll_gap == 0
    assert edl.params_for({"mode": "clean"}) == edl.EdlParams()


def test_split_at_cuts_folds_whip_pan_slivers():
    assert edl._split_at_cuts(0.0, 4.0, [1.0, 1.1, 3.0], 0.3) == [(0.0, 1.1), (1.1, 3.0), (3.0, 4.0)]


def test_vetoed_shot_is_dropped_and_others_kept():
    words = W(("one", 1.0, 1.5), ("two", 3.5, 4.0))
    p = edl.EdlParams(broll_gap=5.0, min_shot=0.5)
    # The pause 1.62-3.42 holds two shots split at 2.5; veto the second one.
    ranges = edl.build_ranges(words, [0, 1], 20.0, p, cuts=[2.5], allow=lambda s, e: s < 2.5)
    assert ranges == [[0.92, 2.5], [3.42, 4.12]]


def test_broll_pieces_carry_surrounding_words():
    words = W(("Look.", 1.0, 1.5), ("Wow.", 4.0, 4.5))
    t = {"words": words, "segments": [{"id": 0, "words": [0, 1]}, {"id": 1, "words": [1, 2]}], "duration": 10.0}
    edits = {"mode": "clean", "opts": {"content": "vlog"}, "order": [0, 1]}
    gap = [pc for pc in edl.broll_pieces(t, edits) if pc["kind"] == "gap"]
    assert len(gap) == 1 and gap[0]["prev_word"] == 0 and gap[0]["next_word"] == 1


def test_review_batch_maps_decisions_and_skips_unanswered(monkeypatch):
    from server.pipeline import broll, narrate

    seen = {}

    def fake_call(system, blocks, schema, effort):
        seen["images"] = sum(b["type"] == "image" for b in blocks)
        return {"decisions": [{"id": "[b0]", "keep": True, "why": "reaction"}]}

    monkeypatch.setattr(narrate, "_call", fake_call)
    words = W(("Look.", 1.0, 1.5), ("Wow.", 4.0, 4.5))
    t = {"words": words, "segments": [], "duration": 10.0}
    pieces = [{"output": "main", "kind": "gap", "start": 1.62, "end": 2.5, "prev_word": 0, "next_word": 1},
              {"output": "main", "kind": "gap", "start": 2.5, "end": 3.92, "prev_word": 0, "next_word": 1}]
    out = broll._review_batch([("b0", pieces[0], ["x"]), ("b1", pieces[1], ["y", "z"])], t, {"opts": {"content": "vlog"}})
    assert seen["images"] == 3
    assert out == {"1.620-2.500": {"keep": True, "why": "reaction"}, "2.500-3.920": {"keep": False, "why": "no decision"}}


def test_talking_head_preset_closes_short_pauses():
    words = W(("one", 1.0, 1.5), ("two", 1.9, 2.4))  # a 0.4s breath
    screen = edl.build_ranges(words, [0, 1], 10.0, edl.params_for({"opts": {"content": "screen"}}))
    talking = edl.build_ranges(words, [0, 1], 10.0, edl.params_for({"opts": {"content": "talking"}}))
    assert len(screen) == 1  # kept whole
    assert len(talking) == 2 and talking[1][0] - talking[0][1] > 0.2  # breath removed
