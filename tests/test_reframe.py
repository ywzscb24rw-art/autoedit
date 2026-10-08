from server.pipeline import reframe
from server.pipeline.reframe import Face

CROP = 0.32  # 9:16 crop of a 16:9 frame is ~32% of the width


def test_single_face_is_centred():
    assert reframe.subject_x([Face(cx=0.7, w=0.1, area=0.02)], CROP) == 0.7


def test_two_faces_that_fit_are_both_framed():
    faces = [Face(cx=0.4, w=0.08, area=0.02), Face(cx=0.55, w=0.08, area=0.018)]
    assert abs(reframe.subject_x(faces, CROP) - 0.475) < 1e-9


def test_faces_too_far_apart_follow_the_largest():
    faces = [Face(cx=0.1, w=0.1, area=0.01), Face(cx=0.9, w=0.12, area=0.03)]
    assert reframe.subject_x(faces, CROP) == 0.9


def test_small_background_faces_are_ignored():
    faces = [Face(cx=0.3, w=0.15, area=0.05), Face(cx=0.95, w=0.02, area=0.001)]
    assert reframe.subject_x(faces, CROP) == 0.3


def test_steady_subject_gives_one_hold():
    hs = reframe.holds([0.60, 0.62, None, 0.58, 0.61], CROP)
    assert len(hs) == 1 and abs(hs[0][2] - 0.60) < 0.02


def test_head_bobbing_does_not_move_the_camera():
    # Back-and-forth motion inside the safe zone: the old follower hunted here.
    bob = [0.55, 0.62, 0.56, 0.63, 0.55, 0.62, 0.57, 0.63] * 3
    assert len(reframe.holds(bob, CROP)) == 1


def test_one_sample_glitch_is_ignored():
    glitch = [0.3] * 6 + [0.9] + [0.3] * 6  # stray face for one sample
    assert len(reframe.holds(glitch, CROP)) == 1


def test_subject_leaving_the_zone_gives_one_eased_move():
    targets = [0.25] * 12 + [0.75] * 12
    path = reframe.camera_path(targets, [0], CROP)
    xs = [x for _, x in path]
    steps = [b - a for a, b in zip(xs, xs[1:])]
    assert all(st >= 0 for st in steps)  # one direction, no hunting
    assert abs(xs[0] - 0.25) < 1e-9 and abs(xs[-1] - 0.75) < 1e-9
    moving = [st for st in steps if st > 0]
    # Eased: the first and last steps of the move are much smaller than the middle ones.
    assert moving[0] < max(moving) / 4 and moving[-1] < max(moving) / 4
    duration = path[-1][0] - path[1][0]
    assert reframe.MOVE_MIN <= duration + 1e-9 <= reframe.MOVE_MAX + 1e-9


def test_no_faces_keeps_previous_framing_not_centre():
    path = reframe.camera_path([0.2, 0.2, 0.2, None, None, None], [0, 3], CROP)
    assert all(abs(x - 0.2) < 1e-9 for _, x in path)


def test_no_faces_anywhere_falls_back_to_centre():
    assert reframe.holds([None, None], CROP) == [(0, 2, 0.5)]


def test_hard_cut_jumps_without_easing():
    path = reframe.camera_path([0.2, 0.2, 0.2, 0.8, 0.8, 0.8], [0, 3], CROP)
    assert path == [(0.0, 0.2), (0.5, 0.8)]


def test_keyframes_only_emit_changes():
    keys = reframe.keyframes([(0.0, 0.5), (0.5, 0.5), (1.0, 0.6)], width=3840, crop_px=1216)
    assert [x for _, x in keys] == [1312, 1696]


def test_split_shots_ignores_shake_but_catches_cuts():
    dark, shaky_dark, bright = bytes([40] * 576), bytes([40 + (i % 2) * 6 for i in range(576)]), bytes([200] * 576)
    assert reframe.split_shots([dark, shaky_dark, dark, bright, bright]) == [0, 3]


def test_strong_only_rides_through_whip_pans():
    # Big pixel change, moderate histogram change: a whip pan, not a cut.
    a = bytes([40] * 288 + [120] * 288)
    b = bytes([120] * 288 + [40] * 200 + [90] * 88)
    assert reframe.split_shots([a, b]) == [0, 1]
    assert reframe.split_shots([a, b], strong_only=True) == [0]


def test_crop_filter_static_and_moving(tmp_path):
    assert reframe.crop_filter([(0.0, 100)], 3840, 2160, tmp_path) == "crop=1216:2160:100:0,scale=1080:1920"
    f = reframe.crop_filter([(0.0, 100), (1.0, 300)], 3840, 2160, tmp_path)
    assert f.startswith("sendcmd=") and "crop=1216:2160:100:0" in f
    assert (tmp_path / "crop.cmd").read_text() == "1.000 crop x 300;\n"


def test_fast_sweep_becomes_one_smooth_move():
    # Subject sweeps across the frame in 1s (a camera swing), between two steady holds.
    sweep = [0.15] * 12 + [0.15 + 0.1 * i for i in range(1, 7)] + [0.8] * 12
    path = reframe.camera_path(sweep, [0], CROP)
    xs = [x for _, x in path]
    steps = [b - a for a, b in zip(xs, xs[1:]) if b != a]
    # A single eased move: speed rises then falls exactly once.
    peak = steps.index(max(steps))
    assert all(x <= y + 1e-12 for x, y in zip(steps[:peak], steps[1 : peak + 1]))
    assert all(x >= y - 1e-12 for x, y in zip(steps[peak:], steps[peak + 1 :]))
