from server.pipeline import captions, reframe, render
from server.pipeline.reframe import Face
from tests.test_edl import W


def test_pages_group_up_to_three_words_and_break_on_punctuation_and_pauses():
    words = W(("So", 0.0, 0.2), ("here's", 0.25, 0.5), ("the", 0.55, 0.6), ("thing.", 0.65, 1.0),
              ("Nobody", 1.1, 1.4), ("knows", 2.5, 2.8))
    pg = captions.pages(words)
    assert [[w["text"] for w in p["words"]] for p in pg] == [["SO", "HERE'S", "THE"], ["THING"], ["NOBODY"], ["KNOWS"]]
    assert pg[0]["end"] == 0.65  # held until the next caption starts
    assert abs(pg[2]["end"] - 1.7) < 1e-9  # long pause after: lingers 0.3s then clears


def test_long_words_split_by_characters():
    words = W(("unbelievably", 0, 0.5), ("extraordinary", 0.5, 1.0))
    assert len(captions.pages(words)) == 2


def test_caption_track_covers_the_piece_exactly(tmp_path):
    words = W(("Hello", 10.0, 10.4), ("there", 10.5, 10.9))
    listing = captions.track(words, 9.5, 11.5, 1920, 1080, tmp_path)
    lines = listing.read_text().splitlines()
    durations = [float(line.split()[1]) for line in lines if line.startswith("duration")]
    assert abs(sum(durations) - 2.0) < 1e-6
    assert len({line for line in lines if line.startswith("file")}) == 3  # blank, HELLO*, THERE*


def test_caption_track_is_none_without_speech(tmp_path):
    assert captions.track(W(("Hi", 0.0, 0.3)), 5.0, 6.0, 1920, 1080, tmp_path) is None


def test_caption_strip_is_transparent_except_text():
    img = captions.draw(["HELLO"], 0, 1080, 1920)
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0
    assert img.getchannel("A").getbbox() is not None


def test_punch_plan_alternates_but_not_into_short_pieces():
    assert render.punch_plan([3.0, 2.0, 0.4, 2.5, 1.0]) == [1.0, 1.15, 1.15, 1.0, 1.15]


def test_zoom_box_frames_face_on_eye_line_and_stays_in_frame():
    cw, ch, x, y = reframe.zoom_box(1920, 1080, 1920, 1080, 1.15, Face(cx=0.5, w=0.1, area=0.01, cy=0.4))
    assert (cw, ch) == (1672, 940)
    assert x == 124 and abs((432 - y) / ch - reframe.EYE_LINE) < 0.01
    # A face at the very edge still gives a crop inside the frame.
    cw, ch, x, y = reframe.zoom_box(1920, 1080, 1920, 1080, 1.15, Face(cx=0.99, w=0.1, area=0.01, cy=0.05))
    assert 0 <= x <= 1920 - cw and 0 <= y <= 1080 - ch


def test_zoom_one_is_the_full_frame():
    assert reframe.zoom_box(1920, 1080, 1920, 1080, 1.0, None) == (1920, 1080, 0, 0)


def test_unpunctuated_sentence_start_after_a_pause_starts_a_new_caption():
    words = W(("making", 0.0, 0.3), ("money", 0.35, 0.6), ("Let", 0.8, 0.9), ("the", 0.95, 1.0))
    assert [[w["text"] for w in p["words"]] for p in captions.pages(words)] == [["MAKING", "MONEY"], ["LET", "THE"]]


def test_names_and_I_mid_sentence_do_not_break():
    words = W(("moved", 0.0, 0.3), ("to", 0.32, 0.4), ("Miami", 0.42, 0.8), ("and", 1.0, 1.1), ("I", 1.3, 1.35), ("think", 1.4, 1.6))
    texts = [[w["text"] for w in p["words"]] for p in captions.pages(words)]
    assert texts == [["MOVED", "TO", "MIAMI"], ["AND", "I", "THINK"]]


def test_caption_line_must_be_low_and_match_speech():
    from server.pipeline import burnin

    spoken = burnin._tokens("so the target revenue for this month is huge")
    assert burnin.caption_matches([("TARGET REVENUE THIS", 0.85)], spoken) == {"target", "revenue", "this"}
    assert burnin.caption_matches([("Target Revenue", 0.30)], spoken) is None  # whiteboard, upper frame
    assert burnin.caption_matches([("Subscribe now", 0.85)], spoken) is None  # graphic, unrelated


def test_static_text_that_matches_speech_is_not_captions():
    from server.pipeline import burnin

    board = frozenset({"target", "revenue"})
    assert not burnin.decide([board] * 8 + [None] * 4)  # same text every time: a sign or board
    varied = [frozenset({f"w{i}", f"x{i}"}) for i in range(8)] + [None] * 4
    assert burnin.decide(varied)
    assert not burnin.decide(varied[:2] + [None] * 10)  # an intro with captions isn't the whole video


def test_burned_captions_turn_caption_default_off_but_explicit_choice_wins():
    from server.pipeline import run

    talking = {"opts": {"content": "talking"}}
    assert run.render_style(talking, "main")["captions"] is True
    assert run.render_style(talking, "main", burned_captions=True)["captions"] is False
    forced = {"opts": {"content": "talking", "captions": True}}
    assert run.render_style(forced, "main", burned_captions=True)["captions"] is True
