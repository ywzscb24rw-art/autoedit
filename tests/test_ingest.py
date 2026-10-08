from fractions import Fraction

from server.pipeline.ingest import can_remux, target_fps
from server.pipeline.render import snap


def test_target_fps_snaps_to_standard_rates():
    assert target_fps(Fraction(24), Fraction(24)) == 24
    assert target_fps(Fraction(30000, 1001), Fraction(30000, 1001)) == Fraction(30000, 1001)
    # Browser webm: nonsense r_frame_rate, ~29.6 average
    assert target_fps(Fraction(1000), Fraction(2960, 100)) == Fraction(30000, 1001)
    assert target_fps(None, None) == 30
    assert target_fps(Fraction(120), Fraction(120)) == 60


def _info(**kw):
    base = {"vcodec": "h264", "pix_fmt": "yuv420p", "cfr": True, "format": "mov,mp4,m4a,3gp,3g2,mj2", "width": 3840}
    return {**base, **kw}


def test_remux_only_browser_safe_constant_rate_h264():
    assert can_remux(_info())
    assert not can_remux(_info(vcodec="hevc"))
    assert not can_remux(_info(cfr=False))
    assert not can_remux(_info(format="matroska,webm"))
    assert not can_remux(_info(pix_fmt="yuv420p10le"))


def test_snap_to_ntsc_frames():
    fps = Fraction(30000, 1001)
    t = snap(10.0, fps)
    assert abs(t * float(fps) - round(t * float(fps))) < 1e-9
