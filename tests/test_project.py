from fractions import Fraction
from pathlib import Path

from penultimate_cut.model.project import STILL_IMAGE_DEFAULT_DURATION, MediaRef, default_clip_for


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
