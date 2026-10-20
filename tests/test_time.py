from fractions import Fraction

from penultimate_cut.model.time import (
    frame_to_seconds,
    rate,
    seconds_to_frame,
    snap_to_frame,
)


def test_rate_snaps_ntsc_float() -> None:
    assert rate(29.97) == Fraction(30000, 1001)


def test_rate_plain_int() -> None:
    assert rate(24) == Fraction(24)


def test_frame_seconds_roundtrip() -> None:
    fps = rate(30)
    for frame in range(0, 300, 7):
        assert seconds_to_frame(frame_to_seconds(frame, fps), fps) == frame


def test_snap_to_frame() -> None:
    fps = rate(30)
    # 1.0005s is between frame 30 (1.0s) and frame 31 (1.0333s); nearer to frame 30.
    assert snap_to_frame(Fraction("1.0005"), fps) == frame_to_seconds(30, fps)
