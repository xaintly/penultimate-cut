from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.model.project import Clip, Gap, MediaRef, Title, Track, new_title_clip
from penultimate_cut.model.timeline_ops import (
    TimelineOpError,
    cut_points,
    delete_item,
    insert_item,
    item_at,
    lift_item,
    move_item,
    next_cut,
    place_item,
    previous_cut,
    remove_gaps,
    remove_media,
    set_clip_options,
    set_fit_mode,
    set_title,
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


def layout(track: Track) -> str:
    """ "a3 _2 b5": clip a.mp4 for 3 s, 2 s of empty space, clip b.mp4 for 5 s."""
    return " ".join(
        f"_{item.duration}"
        if isinstance(item, Gap)
        else f"{item.media_ref.path.stem}{item.duration}"
        for item in track.items
    )


def track_of(text: str) -> Track:
    """The track `layout()` would describe as `text`."""
    items: list[Clip | Gap] = []
    for part in text.split():
        name, duration = part[0], Fraction(part[1:])
        items.append(Gap(duration=duration) if name == "_" else make_clip(f"{name}.mp4", duration))
    return Track(kind="video", items=items)


def test_cut_points_and_jumping_between_them() -> None:
    track = track_of("a3 _2 b5")
    assert cut_points(track) == [0, 3, 5, 10]
    assert next_cut(track, Fraction(0)) == 3
    assert next_cut(track, Fraction(3)) == 5
    assert next_cut(track, Fraction(10)) == 10  # at the end: stays
    assert previous_cut(track, Fraction(4)) == 3
    assert previous_cut(track, Fraction(3)) == 0
    assert previous_cut(track, Fraction(0)) == 0
    # Within the tolerance of a cut counts as on it.
    near = Fraction(3) - Fraction(1, 100)
    assert next_cut(track, near, Fraction(1, 60)) == 5
    assert previous_cut(track, Fraction(3) + Fraction(1, 100), Fraction(1, 60)) == 0
    assert cut_points(Track(kind="video", items=[])) == [0]


def test_place_item_between_clips_pushes_what_follows() -> None:
    track = track_of("a3 b5")
    assert layout(place_item(track, 1, Fraction(0), make_clip("x.mp4", Fraction(2)))) == "a3 x2 b5"
    assert layout(place_item(track, 2, Fraction(0), make_clip("x.mp4", Fraction(2)))) == "a3 b5 x2"


def test_place_item_into_empty_space_fills_it_and_what_follows_stays_put() -> None:
    track = track_of("a3 _10 b5")
    x = make_clip("x.mp4", Fraction(4))
    assert layout(place_item(track, 1, Fraction(0), x)) == "a3 x4 _6 b5"
    assert layout(place_item(track, 1, Fraction(6), x)) == "a3 _6 x4 b5"
    assert layout(place_item(track, 1, Fraction(2), x)) == "a3 _2 x4 _4 b5"


def test_place_item_longer_than_the_empty_space_moves_what_follows_by_the_rest() -> None:
    track = track_of("a3 _2 b5")
    assert layout(place_item(track, 1, Fraction(0), make_clip("x.mp4", Fraction(5)))) == "a3 x5 b5"


def test_place_item_rejects_an_offset_outside_empty_space() -> None:
    track = track_of("a3 _2 b5")
    x = make_clip("x.mp4", Fraction(1))
    for index, offset in ((0, Fraction(1)), (1, Fraction(2)), (1, Fraction(-1)), (4, Fraction(0))):
        with pytest.raises(TimelineOpError):
            place_item(track, index, offset, x)


def test_lift_item_leaves_empty_space_joined_with_its_neighbours() -> None:
    lifted, hole = lift_item(track_of("a3 x2 b5"), 1)
    assert (layout(lifted), hole) == ("a3 _2 b5", 1)
    lifted, hole = lift_item(track_of("a3 _1 x2 _4 b5"), 2)
    assert (layout(lifted), hole) == ("a3 _7 b5", 1)
    lifted, hole = lift_item(track_of("a3 x2"), 1)
    assert (layout(lifted), hole) == ("a3 _2", 1)  # kept at the end, unlike after an edit


def test_move_item_reorders_and_closes_up_where_it_was() -> None:
    track = track_of("a3 x2 b5 c1")
    # Lifted: "a3 _2 b5 c1" -- indices are into that.
    assert layout(move_item(track, 1, 3, Fraction(0))) == "a3 b5 x2 c1"
    assert layout(move_item(track, 1, 4, Fraction(0))) == "a3 b5 c1 x2"
    assert layout(move_item(track, 1, 0, Fraction(0))) == "x2 a3 b5 c1"
    # Back where it was: no change.
    assert move_item(track, 1, 1, Fraction(0)) is track


def test_move_item_into_empty_space_elsewhere_fills_it() -> None:
    track = track_of("x2 a3 _10 b5")
    # Lifted: "_2 a3 _10 b5". The spot x left closes up; the space it lands in shrinks.
    assert layout(move_item(track, 0, 2, Fraction(0))) == "a3 x2 _8 b5"
    assert layout(move_item(track, 0, 2, Fraction(8))) == "a3 _8 x2 b5"


def test_move_item_within_its_own_empty_space_just_slides() -> None:
    track = track_of("a3 x2 _6 b5")
    # Lifted: "a3 _8 b5": sliding x 3 s later leaves everything else in place.
    assert layout(move_item(track, 1, 1, Fraction(3))) == "a3 _3 x2 _3 b5"
    assert layout(move_item(track, 1, 1, Fraction(6))) == "a3 _6 x2 b5"
    assert move_item(track, 1, 1, Fraction(0)) is track


def test_move_item_of_the_last_clip_past_the_end_stays_put() -> None:
    track = track_of("a3 _1 x2")
    # Lifted: "a3 _3" (kept at the end); x is dropped into its own space.
    assert layout(move_item(track, 2, 1, Fraction(1))) == "a3 _1 x2"
    assert layout(move_item(track, 2, 0, Fraction(0))) == "x2 a3"


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


def test_remove_media_leaves_empty_space_for_its_clips() -> None:
    keep = make_clip("keep.mp4", duration=Fraction(2))
    gone_a = make_clip("gone.mp4", duration=Fraction(3))
    gone_b = replace(gone_a, id="other-clip-same-media")
    track = Track(kind="video", items=[gone_a, keep, Gap(duration=Fraction(1)), gone_b])
    new_track = remove_media(track, gone_a.media_ref.id)
    # Leading clip -> gap; the trailing gap + clip vanish entirely.
    assert len(new_track.items) == 2
    assert isinstance(new_track.items[0], Gap) and new_track.items[0].duration == Fraction(3)
    assert new_track.items[1] is keep


def test_set_clip_options_changes_only_what_is_given() -> None:
    track = Track(kind="video", items=[make_clip()])
    loud = set_clip_options(track, 0, volume=150)
    muted = set_clip_options(loud, 0, muted=True, fade_in=True)

    clip = muted.items[0]
    assert isinstance(clip, Clip)
    assert (clip.volume, clip.muted, clip.fade_in, clip.fade_out) == (150, True, True, False)
    assert clip.gain == 0
    assert track.items[0].volume == 100  # the original track is untouched


def test_set_clip_options_rejects_gaps_and_out_of_range_volume() -> None:
    track = Track(kind="video", items=[make_clip(), Gap(duration=Fraction(2))])
    with pytest.raises(TimelineOpError):
        set_clip_options(track, 0, volume=201)
    with pytest.raises(TimelineOpError):
        set_clip_options(track, 0, volume=-1)
    with pytest.raises(TimelineOpError):
        set_clip_options(track, 1, muted=True)


def test_split_keeps_fades_at_the_outer_ends() -> None:
    clip = replace(make_clip(), fade_in=True, fade_out=True, volume=40)
    first, second = split_at(Track(kind="video", items=[clip]), Fraction(4)).items
    assert isinstance(first, Clip) and isinstance(second, Clip)
    assert (first.fade_in, first.fade_out) == (True, False)
    assert (second.fade_in, second.fade_out) == (False, True)
    assert first.volume == second.volume == 40


def test_set_title_gives_the_clip_its_own_new_media() -> None:
    title_clip = new_title_clip()
    other = make_clip()
    track = Track(kind="video", items=[title_clip, other])
    split = split_at(track, Fraction(2))  # both halves share one title

    changed = set_title(split, 1, Title(text="Part two"))

    first, second = changed.items[0], changed.items[1]
    assert isinstance(first, Clip) and isinstance(second, Clip)
    assert first.media_ref.title == Title()  # the other half keeps its text
    assert second.media_ref.title == Title(text="Part two")
    assert second.media_ref.id != first.media_ref.id
    assert second.id == split.items[1].id  # still the same clip
    assert second.fade_out and (second.source_in, second.source_out) == (
        split.items[1].source_in,
        split.items[1].source_out,
    )
    assert split.items[1].media_ref.title == Title()  # input untouched


def test_set_title_rejects_anything_but_a_title() -> None:
    track = Track(kind="video", items=[make_clip(), Gap(duration=Fraction(1))])
    for index in (0, 1):
        with pytest.raises(TimelineOpError):
            set_title(track, index, Title())
