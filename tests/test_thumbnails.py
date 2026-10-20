from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.media.probe import probe
from penultimate_cut.media.thumbnails import filmstrip, thumbnail


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache_root"))


def test_filmstrip_returns_requested_count_of_real_files(make_video_clip) -> None:
    path = make_video_clip("filmstrip_basic.mp4", duration=3.0, fps=30, size="320x240")
    info = probe(path)

    frames = filmstrip(path, info.duration, count=4, width=80)

    assert len(frames) == 4
    for frame in frames:
        assert frame.exists()
        assert frame.stat().st_size > 0
    # four distinct timestamps across a real video -> four distinct frame files
    assert len({f.name for f in frames}) == 4


def test_thumbnail_returns_one_frame_from_the_middle(make_video_clip) -> None:
    path = make_video_clip("thumb_basic.mp4", duration=2.0, fps=30, size="320x240")
    info = probe(path)

    frame = thumbnail(path, info.duration, width=80)

    assert frame.exists()
    assert frame.stat().st_size > 0


def test_filmstrip_does_not_regenerate_cached_frames(make_video_clip) -> None:
    path = make_video_clip("filmstrip_cache.mp4", duration=2.0, fps=30, size="320x240")
    info = probe(path)

    frames = filmstrip(path, info.duration, count=2, width=80)
    sentinel = b"sentinel-bytes-proving-this-file-was-reused-not-regenerated"
    frames[0].write_bytes(sentinel)

    frames_again = filmstrip(path, info.duration, count=2, width=80)

    assert frames_again[0].read_bytes() == sentinel


def test_still_image_reuses_one_frame_for_every_slot(make_still_image) -> None:
    path = make_still_image("filmstrip_still.png", size="320x240")
    info = probe(path)
    assert info.is_still_image

    frames = filmstrip(path, info.duration, count=5, is_still_image=True, width=80)

    assert len(frames) == 5
    assert len(set(frames)) == 1
    assert frames[0].exists()
    assert frames[0].stat().st_size > 0


def test_filmstrip_rejects_non_positive_count(make_video_clip) -> None:
    path = make_video_clip("filmstrip_badcount.mp4", duration=1.0, fps=30, size="320x240")
    info = probe(path)
    with pytest.raises(ValueError):
        filmstrip(path, info.duration, count=0)


def test_filmstrip_start_offset_produces_different_frames(make_video_clip) -> None:
    path = make_video_clip("filmstrip_start.mp4", duration=4.0, fps=30, size="320x240")

    from_the_start = filmstrip(path, duration=Fraction(1), count=1, start=Fraction(0), width=80)
    from_offset = filmstrip(path, duration=Fraction(1), count=1, start=Fraction(2), width=80)

    assert from_the_start[0] != from_offset[0]
    assert from_the_start[0].read_bytes() != from_offset[0].read_bytes()
