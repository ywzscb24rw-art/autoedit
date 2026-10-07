from server.pipeline import clean, edl
from server.pipeline.transcribe import group_segments

P = edl.EdlParams(max_gap=0.6, pad_before=0.08, pad_after=0.12, merge_within=0.05)


def W(*spec):
    """W(("Hello", 0.0, 0.4), ...) -> word dicts"""
    return [{"i": i, "w": " " + w, "start": s, "end": e, "prob": 1.0} for i, (w, s, e) in enumerate(spec)]


def test_contiguous_words_form_one_padded_range():
    words = W(("Hello", 1.0, 1.4), ("there", 1.5, 1.9))
    assert edl.build_ranges(words, [0, 1], 10.0, P) == [[0.92, 2.02]]


def test_long_pause_is_compressed_into_two_ranges():
    words = W(("one", 0.5, 1.0), ("two", 4.0, 4.5))
    assert edl.build_ranges(words, [0, 1], 10.0, P) == [[0.42, 1.12], [3.92, 4.62]]


def test_cut_filler_splits_range_and_padding_does_not_bleed_into_it():
    words = W(("so", 0.0, 0.3), ("um", 0.35, 0.7), ("yes", 0.72, 1.0))
    ranges = edl.build_ranges(words, [0, 2], 10.0, P)
    assert ranges == [[0.0, 0.35], [0.7, 1.12]]


def test_touching_ranges_merge():
    # A cut word with zero duration in between: padding clamps make the ranges touch.
    words = W(("a", 0.0, 0.5), ("uh", 0.5, 0.5), ("b", 0.52, 1.0))
    assert edl.build_ranges(words, [0, 2], 10.0, P) == [[0.0, 1.12]]


def test_reordering_produces_out_of_order_ranges():
    words = W(("a", 0.0, 0.5), ("b", 0.6, 1.0), ("c", 1.1, 1.5))
    ranges = edl.build_ranges(words, [2, 0, 1], 10.0, P)
    assert ranges[0][0] > ranges[1][0]


def test_clamps_to_duration():
    words = W(("end", 9.9, 10.0))
    assert edl.build_ranges(words, [0], 10.0, P) == [[9.82, 10.0]]


def test_auto_cuts_fillers_and_stutters():
    words = W(("Um,", 0, 0.2), ("I", 0.3, 0.4), ("I", 0.45, 0.5), ("think", 0.5, 0.8), ("wh-", 0.9, 1.0), ("what", 1.0, 1.2))
    assert clean.auto_cuts(words) == {"0": "filler", "1": "stutter", "4": "stutter"}


def test_overrides_and_segment_cuts_combine():
    words = W(("Hi.", 0, 0.3), ("Um", 0.4, 0.5), ("bye.", 0.6, 0.9))
    segs = group_segments(words)
    t = {"words": words, "segments": segs, "duration": 5.0}
    edits = {"auto_cuts": {"1": "filler"}, "segment_cuts": {"0": "retake"}, "overrides": {"1": True}}
    cut = edl.word_cut_reasons(t, edits)
    assert cut == {0: "ai"}
    assert edl.compute(t, edits)["main"] == [[0.32, 1.02]]


def test_group_segments_splits_on_sentence_end_and_pause():
    words = W(("One.", 0, 0.3), ("Two", 0.4, 0.6), ("three", 2.5, 2.8))
    segs = group_segments(words)
    assert [s["words"] for s in segs] == [[0, 1], [1, 2], [2, 3]]
    assert words[2]["seg"] == 2
