from fractions import Fraction
from pathlib import Path

from penultimate_cut.model.project import Clip, Gap, MediaRef, Track
from penultimate_cut.ui.timeline.geometry import (
    SNAP_PX,
    TRIM_HANDLE_WIDTH_PX,
    DropTarget,
    TimelineView,
    drop_target,
    hit_test,
    item_x_range,
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


def test_x_for_time_and_time_for_x_are_inverse() -> None:
    view = TimelineView(pixels_per_second=Fraction(50))
    assert view.x_for_time(Fraction(2)) == 100.0
    assert view.time_for_x(100.0) == Fraction(2)


def test_scroll_offset_shifts_the_origin() -> None:
    view = TimelineView(pixels_per_second=Fraction(50), scroll_offset=Fraction(1))
    # The clip at t=1s (the scroll offset) is now at pixel 0.
    assert view.x_for_time(Fraction(1)) == 0.0
    assert view.x_for_time(Fraction(2)) == 50.0


def test_width_for_duration() -> None:
    view = TimelineView(pixels_per_second=Fraction(50))
    assert view.width_for_duration(Fraction(3)) == 150.0


def test_hit_test_body_of_first_clip() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    view = TimelineView(pixels_per_second=Fraction(50))
    hit = hit_test(track, view, 100.0)  # 2s into a 50px/s timeline
    assert hit is not None
    assert hit.index == 0
    assert hit.target == "body"
    assert hit.time == Fraction(2)


def test_hit_test_second_clip() -> None:
    track = Track(
        kind="video", items=[make_clip(duration=Fraction(5)), make_clip("b.mp4", Fraction(5))]
    )
    view = TimelineView(pixels_per_second=Fraction(50))
    hit = hit_test(track, view, 300.0)  # 6s -> second clip, 1s in
    assert hit is not None
    assert hit.index == 1
    assert hit.target == "body"
    assert hit.time == Fraction(6)


def test_hit_test_past_end_of_track_is_none() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    view = TimelineView(pixels_per_second=Fraction(50))
    assert hit_test(track, view, 1000.0) is None


def test_hit_test_before_start_is_none() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    view = TimelineView(pixels_per_second=Fraction(50))
    assert hit_test(track, view, -10.0) is None


def test_hit_test_near_left_edge_is_trim_start() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    view = TimelineView(pixels_per_second=Fraction(50))
    hit = hit_test(track, view, TRIM_HANDLE_WIDTH_PX - 1)
    assert hit is not None
    assert hit.target == "trim_start"
    assert hit.index == 0


def test_hit_test_near_right_edge_is_trim_end() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    view = TimelineView(pixels_per_second=Fraction(50))
    end_x = view.x_for_time(Fraction(5))
    hit = hit_test(track, view, end_x - 1)
    assert hit is not None
    assert hit.target == "trim_end"
    assert hit.index == 0


def test_hit_test_middle_of_short_clip_is_body_not_trim() -> None:
    # A clip wide enough that its middle is well clear of both trim handles.
    track = Track(kind="video", items=[make_clip(duration=Fraction(5))])
    view = TimelineView(pixels_per_second=Fraction(50))
    middle_x = view.x_for_time(Fraction("2.5"))
    hit = hit_test(track, view, middle_x)
    assert hit is not None
    assert hit.target == "body"


def test_hit_test_works_on_a_gap() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(5)), Gap(duration=Fraction(3))])
    view = TimelineView(pixels_per_second=Fraction(50))
    middle_of_gap_x = view.x_for_time(Fraction("6.5"))
    hit = hit_test(track, view, middle_of_gap_x)
    assert hit is not None
    assert hit.index == 1
    assert hit.target == "body"


def test_item_x_range_for_first_and_second_item() -> None:
    track = Track(
        kind="video", items=[make_clip(duration=Fraction(5)), make_clip("b.mp4", Fraction(3))]
    )
    view = TimelineView(pixels_per_second=Fraction(50))
    assert item_x_range(track, view, 0) == (0.0, 250.0)
    assert item_x_range(track, view, 1) == (250.0, 400.0)


# --- drop_target: 10 px per second, so 1 s = 10 px ---------------------------------

VIEW = TimelineView(pixels_per_second=Fraction(10))
TWO = Fraction(2)


def drop(track: Track, x: float, ghost_x: float | None = None) -> DropTarget:
    return drop_target(track, VIEW, x, TWO, ghost_x=ghost_x, frame_rate=Fraction(30))


def test_drop_over_a_clip_goes_to_its_nearer_end() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(10)), make_clip()])
    assert drop(track, 20) == DropTarget(0, Fraction(0), Fraction(0), False)
    assert drop(track, 80) == DropTarget(1, Fraction(0), Fraction(10), False)
    assert drop(track, 120) == DropTarget(1, Fraction(0), Fraction(10), False)
    assert drop(track, 180) == DropTarget(2, Fraction(0), Fraction(20), False)


def test_drop_past_the_end_or_on_an_empty_track_goes_at_the_end() -> None:
    track = Track(kind="video", items=[make_clip(duration=Fraction(10))])
    assert drop(track, 500) == DropTarget(1, Fraction(0), Fraction(10), False)
    assert drop(Track(kind="video", items=[]), 50) == DropTarget(0, Fraction(0), Fraction(0), False)


def test_drop_at_a_clip_end_touching_empty_space_plugs_it_from_that_side() -> None:
    track = Track(
        kind="video", items=[make_clip(), Gap(duration=Fraction(30)), make_clip()]
    )  # clip 0-10, space 10-40, clip 40-50
    # Right half of the first clip: from the start of the space.
    assert drop(track, 90) == DropTarget(1, Fraction(0), Fraction(10), True)
    # Left half of the last clip: ending where the space ends.
    assert drop(track, 410) == DropTarget(1, Fraction(28), Fraction(38), True)


def test_drop_into_empty_space_goes_where_the_ghost_is_or_snaps_to_an_end() -> None:
    track = Track(kind="video", items=[make_clip(), Gap(duration=Fraction(30)), make_clip()])
    # Well inside: where the ghost's start is (to the frame).
    assert drop(track, 250, ghost_x=200) == DropTarget(1, Fraction(10), Fraction(20), True)
    # Within SNAP_PX of the start, or of the end (the ghost's end at 400).
    near = 100 + SNAP_PX - 1
    assert drop(track, near + 5, ghost_x=near) == DropTarget(1, Fraction(0), Fraction(10), True)
    near_end = 400 - 20 - SNAP_PX + 1
    assert drop(track, near_end + 5, ghost_x=near_end) == DropTarget(
        1, Fraction(28), Fraction(38), True
    )
    # Hanging off either end: kept inside.
    assert drop(track, 120, ghost_x=60).offset == 0
    assert drop(track, 395, ghost_x=390).offset == 28


def test_drop_into_empty_space_too_small_for_it_fills_it() -> None:
    track = Track(kind="video", items=[make_clip(), Gap(duration=Fraction(1)), make_clip()])
    assert drop(track, 105) == DropTarget(1, Fraction(0), Fraction(10), True)


def test_drop_past_empty_space_kept_at_the_end_goes_into_it() -> None:
    # A lifted last clip leaves empty space at the end; past it is still in it.
    track = Track(kind="video", items=[make_clip(), Gap(duration=Fraction(10))])
    assert drop(track, 900, ghost_x=900) == DropTarget(1, Fraction(8), Fraction(18), True)
