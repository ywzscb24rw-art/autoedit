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


def test_steady_subject_gives_a_locked_off_shot():
    path = reframe.plan_shot([0.60, 0.62, None, 0.58, 0.61], CROP)
    assert len(set(path)) == 1 and abs(path[0] - 0.60) < 1e-9


def test_moving_subject_pans_smoothly_without_overshoot():
    targets = [0.2] * 3 + [0.8] * 12
    path = reframe.plan_shot(targets, CROP)
    steps = [b - a for a, b in zip(path, path[1:])]
    assert all(s >= 0 for s in steps)  # monotonic, no jitter
    assert max(path) <= 0.8 and path[-1] > 0.75


def test_no_faces_falls_back_to_centre():
    assert reframe.plan_shot([None, None], CROP) == [0.5, 0.5]


def test_cut_resets_framing_per_shot():
    path = reframe.plan([0.2, 0.2, 0.8, 0.8], [0, 2], CROP)
    assert path == [0.2, 0.2, 0.8, 0.8]


def test_keyframes_jump_at_cuts_and_interpolate_pans():
    keys = reframe.keyframes([0.2, 0.2, 0.8, 0.8], [0, 2], width=3840, crop_px=1214)
    xs = [x for _, x in keys]
    assert len(keys) == 2  # locked shot, instant cut, locked shot
    pan = reframe.keyframes([0.3, 0.5], [0], width=3840, crop_px=1214, steps=4)
    assert len(pan) == 5 and pan[0][1] < pan[2][1] < pan[-1][1]
    assert all(0 <= x <= 3840 - 1214 for x in xs)


def test_split_shots_ignores_shake_but_catches_cuts():
    dark, shaky_dark, bright = bytes([40] * 576), bytes([40 + (i % 2) * 6 for i in range(576)]), bytes([200] * 576)
    assert reframe.split_shots([dark, shaky_dark, dark, bright, bright]) == [0, 3]


def test_crop_filter_static_and_moving(tmp_path):
    assert reframe.crop_filter([(0.0, 100)], 3840, 2160, tmp_path) == "crop=1216:2160:100:0,scale=1080:1920"
    f = reframe.crop_filter([(0.0, 100), (1.0, 300)], 3840, 2160, tmp_path)
    assert f.startswith("sendcmd=") and "crop=1216:2160:100:0" in f
    assert (tmp_path / "crop.cmd").read_text() == "1.000 crop x 300;\n"
