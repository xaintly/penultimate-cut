from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.model.project import Clip, Gap, MediaRef, Track
from penultimate_cut.model.timeline_ops import (
    TimelineOpError,
    delete_item,
    item_at,
    move_item,
    split_at,
    trim_item,
)


def make_clip(path: str = "a.mp4", duration: Fraction = Fraction(10)) -> Clip:
    media = MediaRef(
        path=Path(path),
        relative_path=None,
        duration=duration,
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    return Clip(media_ref=media, source_in=Fraction(0), source_out=duration)


def test_item_at_middle_clip() -> None:
    track = Track(
        kind="video", items=[make_clip(duration=Fraction(5)), make_clip(duration=Fraction(5))]
    )
    index, offset = item_at(track, Fraction(7))
    assert index == 1
    assert offset == Fraction(2)


def test_item_at_out_of_range_raises() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    with pytest.raises(TimelineOpError):
        item_at(track, Fraction(5))


def test_split_at_mid_clip_produces_two_clips_same_total_duration() -> None:
    clip = make_clip(duration=Fraction(10))
    track = Track(kind="video", items=[clip])
    result = track_duration_before = track.duration
    new_track = split_at(track, Fraction(4))
    assert len(new_track.items) == 2
    assert new_track.items[0].duration == Fraction(4)
    assert new_track.items[1].duration == Fraction(6)
    assert new_track.duration == result == track_duration_before


def test_split_at_boundary_is_noop() -> None:
    track = Track(
        kind="video", items=[make_clip(duration=Fraction(5)), make_clip(duration=Fraction(5))]
    )
    new_track = split_at(track, Fraction(5))
    assert new_track is track


def test_delete_ripple_closes_gap() -> None:
    track = Track(
        kind="video",
        items=[
            make_clip(duration=Fraction(5)),
            make_clip(duration=Fraction(3)),
            make_clip(duration=Fraction(2)),
        ],
    )
    new_track = delete_item(track, 1, ripple=True)
    assert len(new_track.items) == 2
    assert new_track.duration == Fraction(7)


def test_delete_leave_gap_preserves_total_duration() -> None:
    track = Track(
        kind="video", items=[make_clip(duration=Fraction(5)), make_clip(duration=Fraction(3))]
    )
    new_track = delete_item(track, 0, ripple=False)
    assert isinstance(new_track.items[0], Gap)
    assert new_track.items[0].duration == Fraction(5)
    assert new_track.duration == Fraction(8)


def test_trim_start_shrinks_clip_and_shifts_later_items() -> None:
    track = Track(
        kind="video", items=[make_clip(duration=Fraction(10)), make_clip(duration=Fraction(5))]
    )
    new_track = trim_item(track, 0, "start", Fraction(2))
    assert new_track.items[0].duration == Fraction(8)
    assert new_track.duration == Fraction(13)


def test_trim_end_past_source_media_raises() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(10))])
    with pytest.raises(TimelineOpError):
        trim_item(track, 0, "end", Fraction(11))


def test_move_item_reorders() -> None:
    clip_a, clip_b = make_clip("a.mp4"), make_clip("b.mp4")
    track = Track(kind="video", items=[clip_a, clip_b])
    new_track = move_item(track, 0, 1)
    assert new_track.items == [clip_b, clip_a]
