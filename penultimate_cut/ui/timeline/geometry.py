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

from penultimate_cut.model.project import Gap, Track
from penultimate_cut.model.time import fraction_from_seconds, snap_to_frame
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


SNAP_PX = 30.0
"""Dropped into empty space this close to either end of it, a clip snaps to
that end, so plugging a hole doesn't take a steady hand. In pixels, so it
feels the same at any zoom."""


@dataclass(frozen=True)
class DropTarget:
    """Where a drop would put something: `index`/`offset` as
    `timeline_ops.place_item` takes them."""

    index: int
    offset: Fraction
    time: Fraction
    """Where the dropped item would start, on the track as shown."""
    into_space: bool
    """It lands in empty space, exactly at `time` -- so that's where the
    ghost of it is drawn, rather than under the pointer."""


def drop_target(
    track: Track,
    view: TimelineView,
    x: float,
    duration: Fraction,
    *,
    ghost_x: float | None = None,
    frame_rate: Fraction | None = None,
) -> DropTarget:
    """Where something `duration` long goes if dropped with the pointer at
    pixel `x` and its own start at `ghost_x` (where it was grabbed decides
    the difference; default `x`). For a clip being moved, `track` is the
    track with it lifted out (`timeline_ops.lift_item`).

    Over a clip, it goes in front of or after it, whichever end is nearer;
    if that end touches empty space, into that space, plugging it from
    that side. Over empty space, it goes where its ghost is -- or snapped
    to either end of the space, near one (`SNAP_PX`).
    """
    items = track.items
    pointer = max(Fraction(0), view.time_for_x(x))
    start = Fraction(0)
    index = len(items)
    for i, item in enumerate(items):
        if pointer < start + item.duration:
            index = i
            break
        start += item.duration
    if index == len(items):
        if not items or not isinstance(items[-1], Gap):
            return DropTarget(len(items), Fraction(0), track.duration, False)
        index = len(items) - 1  # past empty space kept at the end: into it
        start -= items[-1].duration

    item = items[index]
    if isinstance(item, Gap):
        room = item.duration - duration
        offset = Fraction(0)
        if room > 0:
            wanted = view.time_for_x(x if ghost_x is None else ghost_x) - start
            if frame_rate:
                wanted = snap_to_frame(wanted, frame_rate)
            offset = max(Fraction(0), min(room, wanted))
            if view.width_for_duration(offset) <= SNAP_PX:
                offset = Fraction(0)
            elif view.width_for_duration(room - offset) <= SNAP_PX:
                offset = room
        return DropTarget(index, offset, start + offset, True)

    after = pointer >= start + item.duration / 2
    boundary = index + 1 if after else index
    boundary_time = start + item.duration if after else start
    if boundary < len(items) and isinstance(items[boundary], Gap):
        return DropTarget(boundary, Fraction(0), boundary_time, True)
    if boundary > 0 and isinstance(items[boundary - 1], Gap):
        space = items[boundary - 1].duration
        offset = max(Fraction(0), space - duration)
        return DropTarget(boundary - 1, offset, boundary_time - space + offset, True)
    return DropTarget(boundary, Fraction(0), boundary_time, False)


def item_x_range(track: Track, view: TimelineView, index: int) -> tuple[float, float]:
    """Pixel `(start, end)` of `track.items[index]`, for painting."""
    start_time = sum((item.duration for item in track.items[:index]), Fraction(0))
    item = track.items[index]
    start_x = view.x_for_time(start_time)
    return start_x, start_x + view.width_for_duration(item.duration)
