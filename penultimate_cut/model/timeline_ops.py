"""Pure functions that transform a `Track`: split, trim, move, delete, insert.

Every function takes a `Track` and returns a *new* `Track`; none mutate their
argument. That makes before/after snapshots trivial to capture for undo.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Literal
from uuid import uuid4

from penultimate_cut.model.project import Clip, FitMode, Gap, Track, TrackItem


class TimelineOpError(ValueError):
    """Raised when an op can't be applied (out of range, trims past source media, etc.)."""


def item_at(track: Track, position: Fraction) -> tuple[int, Fraction]:
    """Return `(index, offset)` of the item spanning `position`.

    `offset` is the time into that item, in `[0, item.duration)`. Raises if
    `position` is outside the track (or exactly at its end).
    """
    cursor = Fraction(0)
    for index, item in enumerate(track.items):
        if cursor <= position < cursor + item.duration:
            return index, position - cursor
        cursor += item.duration
    raise TimelineOpError(f"position {position} is outside the track (duration {cursor})")


def split_at(track: Track, position: Fraction) -> Track:
    """Split the clip spanning `position` into two clips at that point.

    A no-op (returns `track` unchanged) if `position` lands exactly on an
    existing item boundary — there's nothing to split.
    """
    index, offset = item_at(track, position)
    item = track.items[index]
    if offset == 0:
        return track
    if isinstance(item, Gap):
        first: TrackItem = Gap(duration=offset)
        second: TrackItem = Gap(duration=item.duration - offset)
    else:
        split_source = item.source_in + offset * item.speed
        first = replace(item, source_out=split_source)
        # A fresh id: the two halves are separate clips from here on, and
        # anything keyed by clip id (e.g. the timeline's thumbnail cache)
        # must be able to tell them apart.
        second = replace(item, source_in=split_source, id=uuid4().hex)
    new_items = [*track.items[:index], first, second, *track.items[index + 1 :]]
    return replace(track, items=new_items)


def delete_item(track: Track, index: int, *, ripple: bool) -> Track:
    """Remove `track.items[index]`.

    `ripple=True` closes the gap (later items shift earlier). `ripple=False`
    leaves a `Gap` of the same duration in its place, merged with any gap
    already next to it (so one stretch of empty space is always one `Gap`),
    and dropped entirely if it ends up at the very end of the track --
    trailing empty space would only add black to the end of the video.

    Deleting a `Gap` always closes it, whatever `ripple` says: replacing
    empty space with the same empty space would do nothing.
    """
    _check_index(track, index)
    item = track.items[index]
    if ripple or isinstance(item, Gap):
        new_items = [*track.items[:index], *track.items[index + 1 :]]
    else:
        new_items = [*track.items[:index], Gap(duration=item.duration), *track.items[index + 1 :]]
        new_items = _merge_adjacent_gaps(new_items)
        while new_items and isinstance(new_items[-1], Gap):
            new_items.pop()
    return replace(track, items=new_items)


def remove_gaps(track: Track) -> Track:
    """Remove every `Gap`, closing up all empty space in the track."""
    return replace(track, items=[item for item in track.items if not isinstance(item, Gap)])


def _merge_adjacent_gaps(items: list[TrackItem]) -> list[TrackItem]:
    merged: list[TrackItem] = []
    for item in items:
        previous = merged[-1] if merged else None
        if isinstance(item, Gap) and isinstance(previous, Gap):
            merged[-1] = replace(previous, duration=previous.duration + item.duration)
        else:
            merged.append(item)
    return merged


def trim_item(track: Track, index: int, edge: Literal["start", "end"], new_time: Fraction) -> Track:
    """Move a clip's `start` or `end` trim handle to `new_time` (in source media time).

    Shifts every later item in the track, since items are stored back-to-back
    with no absolute position (a "ripple trim"). Raises if the new handle
    would cross the clip's other edge or go outside the source media.
    """
    _check_index(track, index)
    item = track.items[index]
    if not isinstance(item, Clip):
        raise TimelineOpError("cannot trim a gap")

    if edge == "start":
        if not (0 <= new_time < item.source_out):
            raise TimelineOpError("trim start must stay within the source media and before the end")
        new_item = replace(item, source_in=new_time)
    else:
        if not (item.source_in < new_time <= item.media_ref.duration):
            raise TimelineOpError("trim end must stay within the source media and after the start")
        new_item = replace(item, source_out=new_time)

    new_items = [*track.items[:index], new_item, *track.items[index + 1 :]]
    return replace(track, items=new_items)


def set_fit_mode(track: Track, index: int, fit_mode: FitMode) -> Track:
    """Change a clip's Fit Mode (black bars / blurred background / crop to
    fill) -- how it's letterboxed when its aspect ratio doesn't match the
    project's. See CLAUDE.md's Export section."""
    _check_index(track, index)
    item = track.items[index]
    if not isinstance(item, Clip):
        raise TimelineOpError("a gap has no fit mode")
    new_item = replace(item, fit_mode=fit_mode)
    new_items = [*track.items[:index], new_item, *track.items[index + 1 :]]
    return replace(track, items=new_items)


def insert_item(track: Track, index: int, item: TrackItem) -> Track:
    """Insert `item` at `index` (0..len(items), where len(items) means append
    at the end). Used to place a newly-imported clip onto the track."""
    if not (0 <= index <= len(track.items)):
        raise TimelineOpError(f"index {index} out of range for track of {len(track.items)} items")
    new_items = [*track.items[:index], item, *track.items[index:]]
    return replace(track, items=new_items)


def move_item(track: Track, from_index: int, to_index: int) -> Track:
    """Reorder an item within the track (drag-to-reorder)."""
    _check_index(track, from_index)
    if not (0 <= to_index < len(track.items)):
        raise TimelineOpError(f"to_index {to_index} out of range")
    items = list(track.items)
    item = items.pop(from_index)
    items.insert(to_index, item)
    return replace(track, items=items)


def _check_index(track: Track, index: int) -> None:
    if not (0 <= index < len(track.items)):
        raise TimelineOpError(f"index {index} out of range for track of {len(track.items)} items")
