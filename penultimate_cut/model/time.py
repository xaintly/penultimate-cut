"""Rational time helpers.

All durations and positions are `Fraction` seconds, never floats, so repeated
edits land on exact frame boundaries instead of drifting.
"""

from __future__ import annotations

from fractions import Fraction

Rate = Fraction
"""A frame rate, e.g. `Fraction(30000, 1001)` for 29.97 fps or `Fraction(24)` for 24 fps."""


def rate(value: int | float | str) -> Rate:
    """Build a frame rate from a plain number, snapping common NTSC rates to their exact ratio."""
    ntsc = {
        23.976: Fraction(24000, 1001),
        29.97: Fraction(30000, 1001),
        59.94: Fraction(60000, 1001),
    }
    if isinstance(value, float) and value in ntsc:
        return ntsc[value]
    return Fraction(value)


def frame_duration(fps: Rate) -> Fraction:
    """Duration of one frame, in seconds, at the given rate."""
    return 1 / fps


def seconds_to_frame(seconds: Fraction, fps: Rate) -> int:
    """Round a time to the nearest frame index at the given rate."""
    frames = seconds * fps
    return round(frames)


def frame_to_seconds(frame: int, fps: Rate) -> Fraction:
    """Exact seconds for a frame index at the given rate."""
    return Fraction(frame) / fps


def snap_to_frame(seconds: Fraction, fps: Rate) -> Fraction:
    """Snap a time to the nearest exact frame boundary at the given rate."""
    return frame_to_seconds(seconds_to_frame(seconds, fps), fps)
