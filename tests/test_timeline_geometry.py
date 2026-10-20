from fractions import Fraction
from pathlib import Path

from penultimate_cut.model.project import Clip, Gap, MediaRef, Track
from penultimate_cut.ui.timeline.geometry import (
    TRIM_HANDLE_WIDTH_PX,
    TimelineView,
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
