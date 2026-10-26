from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.model.project import Clip, Gap, MediaRef, Track
from penultimate_cut.model.timeline_ops import (
    TimelineOpError,
    delete_item,
    insert_item,
    item_at,
    move_item,
    remove_gaps,
    set_fit_mode,
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


def test_delete_leave_gap_merges_with_neighbouring_gaps() -> None:
    track = Track(
        kind="video",
        items=[
            make_clip(duration=Fraction(1)),
            Gap(duration=Fraction(2)),
            make_clip(duration=Fraction(3)),
            Gap(duration=Fraction(4)),
            make_clip(duration=Fraction(5)),
        ],
    )
    new_track = delete_item(track, 2, ripple=False)
    assert len(new_track.items) == 3
    assert isinstance(new_track.items[1], Gap)
    assert new_track.items[1].duration == Fraction(9)
    assert new_track.duration == track.duration


def test_delete_leave_gap_at_end_drops_trailing_empty_space() -> None:
    first = make_clip(duration=Fraction(5))
    track = Track(kind="video", items=[first, Gap(duration=Fraction(1)), make_clip()])
    new_track = delete_item(track, 2, ripple=False)
    assert new_track.items == [first]


def test_deleting_a_gap_always_closes_it() -> None:
    clips = [make_clip(duration=Fraction(5)), make_clip(duration=Fraction(3))]
    track = Track(kind="video", items=[clips[0], Gap(duration=Fraction(2)), clips[1]])
    assert delete_item(track, 1, ripple=False).items == clips
    assert delete_item(track, 1, ripple=True).items == clips


def test_remove_gaps_closes_all_empty_space() -> None:
    clips = [make_clip(duration=Fraction(5)), make_clip(duration=Fraction(3))]
    track = Track(
        kind="video",
        items=[Gap(duration=Fraction(1)), clips[0], Gap(duration=Fraction(2)), clips[1]],
    )
    assert remove_gaps(track).items == clips


def test_split_halves_get_distinct_ids() -> None:
    new_track = split_at(Track(kind="video", items=[make_clip()]), Fraction(4))
    assert new_track.items[0].id != new_track.items[1].id


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


def test_insert_item_appends_at_end() -> None:
    existing = make_clip(duration=Fraction(5))
    new_clip = make_clip("b.mp4", duration=Fraction(3))
    track = Track(kind="video", items=[existing])
    new_track = insert_item(track, 1, new_clip)
    assert new_track.items == [existing, new_clip]
    assert new_track.duration == Fraction(8)


def test_insert_item_at_start_shifts_existing() -> None:
    existing = make_clip(duration=Fraction(5))
    new_clip = make_clip("b.mp4", duration=Fraction(3))
    track = Track(kind="video", items=[existing])
    new_track = insert_item(track, 0, new_clip)
    assert new_track.items == [new_clip, existing]


def test_insert_item_into_empty_track() -> None:
    new_clip = make_clip(duration=Fraction(4))
    track = Track(kind="video", items=[])
    new_track = insert_item(track, 0, new_clip)
    assert new_track.items == [new_clip]


def test_insert_item_out_of_range_raises() -> None:
    track = Track(kind="video", items=[make_clip()])
    with pytest.raises(TimelineOpError):
        insert_item(track, 2, make_clip("b.mp4"))


def test_set_fit_mode_changes_only_that_field() -> None:
    clip = make_clip(duration=Fraction(5))
    track = Track(kind="video", items=[clip])
    new_track = set_fit_mode(track, 0, "crop_to_fill")
    new_clip = new_track.items[0]
    assert new_clip.fit_mode == "crop_to_fill"
    assert new_clip.source_in == clip.source_in
    assert new_clip.source_out == clip.source_out
    assert new_clip.id == clip.id


def test_set_fit_mode_on_a_gap_raises() -> None:
    track = Track(kind="video", items=[Gap(duration=Fraction(5))])
    with pytest.raises(TimelineOpError):
        set_fit_mode(track, 0, "crop_to_fill")
