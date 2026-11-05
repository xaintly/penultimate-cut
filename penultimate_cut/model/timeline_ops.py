"""Pure functions that transform a `Track`: split, trim, move, delete, insert.

Every function takes a `Track` and returns a *new* `Track`; none mutate their
argument. That makes before/after snapshots trivial to capture for undo.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Literal
from uuid import uuid4

from penultimate_cut.model.project import MAX_VOLUME, Clip, FitMode, Gap, Title, Track, TrackItem


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
        # Fades stay at the clip's outer ends, not at the new cut.
        first = replace(item, source_out=split_source, fade_out=False)
        # A fresh id: the two halves are separate clips from here on, and
        # anything keyed by clip id (e.g. the timeline's thumbnail cache)
        # must be able to tell them apart.
        second = replace(item, source_in=split_source, fade_in=False, id=uuid4().hex)
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
        new_items = _tidy_gaps(new_items)
    return replace(track, items=new_items)


def remove_media(track: Track, media_id: str) -> Track:
    """Replace every clip of the media `media_id` with empty space -- the
    same as deleting each one (without ripple). For removing that media
    from the project entirely."""
    new_items: list[TrackItem] = [
        Gap(duration=item.duration)
        if isinstance(item, Clip) and item.media_ref.id == media_id
        else item
        for item in track.items
    ]
    return replace(track, items=_tidy_gaps(new_items))


def remove_gaps(track: Track) -> Track:
    """Remove every `Gap`, closing up all empty space in the track."""
    return replace(track, items=[item for item in track.items if not isinstance(item, Gap)])


def _tidy_gaps(items: list[TrackItem]) -> list[TrackItem]:
    """Merge adjacent gaps into one, and drop empty space at the very end."""
    merged: list[TrackItem] = []
    for item in items:
        previous = merged[-1] if merged else None
        if isinstance(item, Gap) and isinstance(previous, Gap):
            merged[-1] = replace(previous, duration=previous.duration + item.duration)
        else:
            merged.append(item)
    while merged and isinstance(merged[-1], Gap):
        merged.pop()
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


def set_clip_options(
    track: Track,
    index: int,
    *,
    volume: int | None = None,
    muted: bool | None = None,
    fade_in: bool | None = None,
    fade_out: bool | None = None,
) -> Track:
    """Change a clip's volume, mute, or fades; options left as None keep
    their current value."""
    _check_index(track, index)
    item = track.items[index]
    if not isinstance(item, Clip):
        raise TimelineOpError("a gap has no sound or fades")
    if volume is not None and not 0 <= volume <= MAX_VOLUME:
        raise TimelineOpError(f"volume must be 0-{MAX_VOLUME}%, not {volume}")
    new_item = replace(
        item,
        volume=item.volume if volume is None else volume,
        muted=item.muted if muted is None else muted,
        fade_in=item.fade_in if fade_in is None else fade_in,
        fade_out=item.fade_out if fade_out is None else fade_out,
    )
    new_items = [*track.items[:index], new_item, *track.items[index + 1 :]]
    return replace(track, items=new_items)


def set_title(track: Track, index: int, title: Title) -> Track:
    """Change a title card's text or colors. The clip gets its own new media
    reference, so the other half of a split title keeps its text."""
    _check_index(track, index)
    item = track.items[index]
    if not isinstance(item, Clip) or not item.media_ref.is_title:
        raise TimelineOpError("only a title card has a title to change")
    new_media = replace(item.media_ref, title=title, id=uuid4().hex)
    new_item = replace(item, media_ref=new_media)
    new_items = [*track.items[:index], new_item, *track.items[index + 1 :]]
    return replace(track, items=new_items)


def insert_item(track: Track, index: int, item: TrackItem) -> Track:
    """Insert `item` at `index` (0..len(items), where len(items) means append
    at the end). Used to place a newly-imported clip onto the track."""
    if not (0 <= index <= len(track.items)):
        raise TimelineOpError(f"index {index} out of range for track of {len(track.items)} items")
    new_items = [*track.items[:index], item, *track.items[index:]]
    return replace(track, items=new_items)


def place_item(track: Track, index: int, offset: Fraction, item: TrackItem) -> Track:
    """Put `item` in front of `track.items[index]` (at the end for
    `len(items)`): what dropping something onto the timeline does.

    Into empty space (`items[index]` is a `Gap`), `item` goes `offset` into
    it and takes up the space it covers, so what follows stays put -- or
    moves on only by however much `item` is longer than the space left.
    Anywhere else it's inserted, and what follows moves on by all of it.
    """
    if not (0 <= index <= len(track.items)):
        raise TimelineOpError(f"index {index} out of range for track of {len(track.items)} items")
    return replace(track, items=_tidy_gaps(_place(list(track.items), index, offset, item)))


def lift_item(track: Track, index: int) -> tuple[Track, int]:
    """`track` with the item at `index` taken out and empty space left in its
    place, joined with any empty space either side -- the track as it is
    while that item is being dragged somewhere else -- and the index of that
    empty space. Unlike everywhere else, empty space at the end is kept."""
    _check_index(track, index)
    items = list(track.items)
    start, end = index, index + 1
    if start > 0 and isinstance(items[start - 1], Gap):
        start -= 1
    if end < len(items) and isinstance(items[end], Gap):
        end += 1
    hole = Gap(duration=sum((item.duration for item in items[start:end]), Fraction(0)))
    return replace(track, items=[*items[:start], hole, *items[end:]]), start


def move_item(track: Track, from_index: int, index: int, offset: Fraction) -> Track:
    """Move the item at `from_index` to `index`/`offset` of the lifted track
    (see `lift_item`, `place_item`): dragging a clip somewhere else.

    Where it was closes up, as in a slideshow -- unless it's dropped back
    into the empty space its own spot is part of: then it just slides
    along, and nothing else moves. Returns `track` itself if that leaves
    everything where it was.
    """
    item = track.items[from_index]
    lifted, hole_index = lift_item(track, from_index)
    if not (0 <= index <= len(lifted.items)):
        raise TimelineOpError(f"index {index} out of range for track of {len(lifted.items)} items")
    items = list(lifted.items)
    if index != hole_index:
        left = items[hole_index].duration - item.duration
        if left > 0:
            items[hole_index] = Gap(duration=left)
        else:
            del items[hole_index]
            if index > hole_index:
                index -= 1
    moved = _tidy_gaps(_place(items, index, offset, item))
    return track if _same_arrangement(moved, track.items) else replace(track, items=moved)


def _same_arrangement(a: list[TrackItem], b: list[TrackItem]) -> bool:
    """The same items in the same places; empty space compares by length."""
    return len(a) == len(b) and all(
        x is y or (isinstance(x, Gap) and isinstance(y, Gap) and x.duration == y.duration)
        for x, y in zip(a, b, strict=True)
    )


def _place(
    items: list[TrackItem], index: int, offset: Fraction, item: TrackItem
) -> list[TrackItem]:
    target = items[index] if index < len(items) else None
    if not isinstance(target, Gap):
        if offset:
            raise TimelineOpError("an offset only goes into empty space")
        return [*items[:index], item, *items[index:]]
    if not (0 <= offset < target.duration):
        raise TimelineOpError(f"offset {offset} is outside the empty space")
    before: list[TrackItem] = [Gap(duration=offset)] if offset else []
    left = target.duration - offset - item.duration
    after: list[TrackItem] = [Gap(duration=left)] if left > 0 else []
    return [*items[:index], *before, item, *after, *items[index + 1 :]]


def _check_index(track: Track, index: int) -> None:
    if not (0 <= index < len(track.items)):
        raise TimelineOpError(f"index {index} out of range for track of {len(track.items)} items")
