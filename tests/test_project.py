from fractions import Fraction
from pathlib import Path

from penultimate_cut.model.project import (
    STILL_IMAGE_DEFAULT_DURATION,
    TITLE_DEFAULT_DURATION,
    Clip,
    Gap,
    MediaRef,
    Project,
    Title,
    Track,
    auto_resolution,
    default_clip_for,
    new_title_clip,
    project_titles,
)


def test_default_clip_for_video_spans_whole_media() -> None:
    media = MediaRef(
        path=Path("a.mp4"),
        relative_path=None,
        duration=Fraction(12),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    clip = default_clip_for(media)
    assert clip.source_in == Fraction(0)
    assert clip.source_out == Fraction(12)
    assert clip.duration == Fraction(12)


def test_default_clip_for_still_image_uses_default_duration() -> None:
    media = MediaRef(
        path=Path("photo.jpg"),
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    clip = default_clip_for(media)
    assert clip.source_in == Fraction(0)
    assert clip.source_out == STILL_IMAGE_DEFAULT_DURATION
    assert clip.duration == STILL_IMAGE_DEFAULT_DURATION


def sized_clip(width: int, height: int, seconds: int) -> Clip:
    media = MediaRef(
        path=Path(f"{width}x{height}.mp4"),
        relative_path=None,
        duration=Fraction(600),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=width,
        height=height,
    )
    return Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(seconds))


def project_of(*items: Clip | Gap) -> Project:
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=list(items))])


def test_auto_resolution_defaults_without_clips() -> None:
    assert auto_resolution(project_of()) == (1280, 720)
    assert auto_resolution(project_of(Gap(duration=Fraction(3)))) == (1280, 720)


def test_auto_resolution_is_not_decided_by_the_first_clip() -> None:
    # A short vertical phone clip first, then mostly landscape footage.
    project = project_of(sized_clip(1080, 1920, 5), sized_clip(1280, 720, 60))
    assert auto_resolution(project) == (1280, 720)


def test_auto_resolution_uses_the_sharpest_clip_of_the_winning_shape() -> None:
    # 854x480 counts as the same 16:9 shape as 1920x1080.
    project = project_of(sized_clip(854, 480, 30), sized_clip(1920, 1080, 10))
    assert auto_resolution(project) == (1920, 1080)


def test_auto_resolution_caps_at_1080p_and_never_upscales() -> None:
    assert auto_resolution(project_of(sized_clip(3840, 2160, 10))) == (1920, 1080)
    assert auto_resolution(project_of(sized_clip(640, 480, 10))) == (640, 480)
    # Phone photos: 4:3 shape kept, capped by the short side.
    assert auto_resolution(project_of(sized_clip(4032, 3024, 10))) == (1440, 1080)


def test_auto_resolution_keeps_an_all_vertical_project_vertical() -> None:
    project = project_of(sized_clip(720, 1280, 20), sized_clip(1080, 1920, 5))
    assert auto_resolution(project) == (1080, 1920)


def test_auto_resolution_tie_goes_to_the_shape_that_comes_first() -> None:
    project = project_of(sized_clip(640, 480, 10), sized_clip(1280, 720, 10))
    assert auto_resolution(project) == (640, 480)


def test_new_title_clip_is_a_faded_still_with_default_title() -> None:
    clip = new_title_clip()
    assert clip.media_ref.title == Title()
    assert clip.media_ref.title.background == "#012456"
    assert clip.media_ref.is_title and clip.media_ref.is_still_image
    assert not clip.media_ref.has_audio
    assert clip.duration == TITLE_DEFAULT_DURATION
    assert clip.fade_in and clip.fade_out


def test_display_name_is_file_name_or_first_line_of_title() -> None:
    assert new_title_clip(Title(text="\n  Our Trip  \nby Sam")).media_ref.display_name == (
        "Title: Our Trip"
    )
    assert new_title_clip(Title(text="  ")).media_ref.display_name == "Title"
    media = MediaRef(
        path=Path("clips/a.mp4"),
        relative_path=None,
        duration=Fraction(1),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    assert media.display_name == "a.mp4"


def test_project_titles_lists_each_title_once_and_ignores_resolution() -> None:
    first, second = Title(text="Start"), Title(text="End")
    track = Track(
        kind="video",
        items=[new_title_clip(first), Gap(duration=Fraction(1)), new_title_clip(second)],
    )
    track.items.append(new_title_clip(first))
    project = Project(frame_rate=Fraction(30), tracks=[track])
    assert project_titles(project) == {first, second}
    # Titles have no size of their own; they're drawn at the project's.
    assert auto_resolution(project) == (1280, 720)
