from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from penultimate_cut.media.probe import probe
from penultimate_cut.media.thumbnails import ThumbnailError, frame_at, thumbnail


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache_root"))


def test_thumbnail_returns_one_frame_from_the_middle(make_video_clip) -> None:
    path = make_video_clip("thumb_basic.mp4", duration=2.0, fps=30, size="320x240")
    info = probe(path)

    frame = thumbnail(path, info.duration, width=80)

    assert frame.exists()
    assert QImage(str(frame)).width() == 80


def test_frame_at_is_the_requested_height_at_the_videos_shape(make_video_clip) -> None:
    path = make_video_clip("frame_size.mp4", duration=2.0, fps=30, size="320x240")

    image = QImage(str(frame_at(path, Fraction(1), height=96)))

    assert (image.width(), image.height()) == (128, 96)


def test_frame_at_different_times_gives_different_frames(make_video_clip) -> None:
    path = make_video_clip("frame_times.mp4", duration=4.0, fps=30, size="320x240")

    early = frame_at(path, Fraction(0), height=48)
    late = frame_at(path, Fraction(3), height=48)

    assert early != late
    assert early.read_bytes() != late.read_bytes()


def test_frame_at_reuses_cached_frames(make_video_clip) -> None:
    path = make_video_clip("frame_cache.mp4", duration=2.0, fps=30, size="320x240")
    frame = frame_at(path, Fraction(1, 2), height=48)
    sentinel = b"sentinel-bytes-proving-this-file-was-reused-not-regenerated"
    frame.write_bytes(sentinel)

    assert frame_at(path, Fraction(1, 2), height=48).read_bytes() == sentinel


def test_still_image_uses_one_frame_for_every_time(make_still_image) -> None:
    path = make_still_image("frame_still.png", size="320x240")

    first = frame_at(path, Fraction(0), height=48, is_still_image=True)
    later = frame_at(path, Fraction(3), height=48, is_still_image=True)

    assert first == later
    assert first.exists()


def test_frame_past_the_end_raises(make_video_clip) -> None:
    path = make_video_clip("frame_past_end.mp4", duration=1.0, fps=30, size="320x240")

    with pytest.raises(ThumbnailError):
        frame_at(path, Fraction(30), height=48)
