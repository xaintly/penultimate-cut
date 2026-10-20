"""Pure timeline geometry: time <-> pixel conversion and hit-testing.

No Qt, no painting -- kept separate from the widget so this math is
unit-testable without a QWidget or display. The widget module is
responsible for turning a `Hit` into an actual mouse-interaction/undo
decision; this module only answers "what's under this pixel."
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from penultimate_cut.model.project import Track
from penultimate_cut.model.time import fraction_from_seconds
from penultimate_cut.model.timeline_ops import TimelineOpError, item_at

HitTarget = Literal["body", "trim_start", "trim_end"]

TRIM_HANDLE_WIDTH_PX = 8.0
"""How close (in pixels) to a clip's edge counts as grabbing its trim handle
rather than its body."""


@dataclass
class TimelineView:
    """The visible window onto a track: zoom and horizontal scroll."""

    pixels_per_second: Fraction
    scroll_offset: Fraction = Fraction(0)
    """Seconds; the time at the viewport's left edge (pixel x=0)."""

    def x_for_time(self, time: Fraction) -> float:
        return float((time - self.scroll_offset) * self.pixels_per_second)

    def time_for_x(self, x: float) -> Fraction:
        seconds_from_left_edge = x / float(self.pixels_per_second)
        return self.scroll_offset + fraction_from_seconds(seconds_from_left_edge)

    def width_for_duration(self, duration: Fraction) -> float:
        return float(duration * self.pixels_per_second)


@dataclass
class Hit:
    index: int
    target: HitTarget
    time: Fraction
    """The exact time under the cursor -- the scrub/seek position for a body
    hit, or the item's own edge time for a trim handle hit."""


def hit_test(track: Track, view: TimelineView, x: float) -> Hit | None:
    """What's under pixel `x`: a clip/gap body, or one of its trim handles.
    `None` past the end of the track or before its start."""
    time = view.time_for_x(x)
    if time < 0:
        return None
    try:
        index, offset = item_at(track, time)
    except TimelineOpError:
        return None

    item = track.items[index]
    item_start_x = view.x_for_time(time - offset)
    item_end_x = item_start_x + view.width_for_duration(item.duration)

    if x - item_start_x <= TRIM_HANDLE_WIDTH_PX:
        return Hit(index=index, target="trim_start", time=time)
    if item_end_x - x <= TRIM_HANDLE_WIDTH_PX:
        return Hit(index=index, target="trim_end", time=time)
    return Hit(index=index, target="body", time=time)


def item_x_range(track: Track, view: TimelineView, index: int) -> tuple[float, float]:
    """Pixel `(start, end)` of `track.items[index]`, for painting."""
    start_time = sum((item.duration for item in track.items[:index]), Fraction(0))
    item = track.items[index]
    start_x = view.x_for_time(start_time)
    return start_x, start_x + view.width_for_duration(item.duration)
