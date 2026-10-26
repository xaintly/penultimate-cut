import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.media.probe import ProbeError, probe


def test_probe_video_with_audio(make_video_clip) -> None:
    path = make_video_clip("basic.mp4", duration=2.0, fps=30, size="320x240")
    info = probe(path)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.width == 320
    assert info.height == 240
    assert info.frame_rate == Fraction(30)
    assert not info.is_still_image
    assert abs(info.duration - Fraction(2)) < Fraction(1, 10)


def test_probe_ntsc_frame_rate_is_exact(make_video_clip) -> None:
    path = make_video_clip("ntsc.mp4", duration=1.0, fps="30000/1001", size="320x240")
    info = probe(path)
    assert info.frame_rate == Fraction(30000, 1001)


def test_probe_audio_only(make_audio_clip) -> None:
    path = make_audio_clip("audio.m4a", duration=1.5)
    info = probe(path)
    assert info.has_video is False
    assert info.has_audio is True
    assert info.width is None
    assert info.height is None
    assert info.frame_rate is None
    assert not info.is_still_image
    assert abs(info.duration - Fraction(3, 2)) < Fraction(1, 10)


def test_probe_still_image(make_still_image) -> None:
    path = make_still_image("photo.png", size="640x480")
    info = probe(path)
    assert info.has_video is True
    assert info.has_audio is False
    assert info.frame_rate is None
    assert info.is_still_image
    assert info.width == 640
    assert info.height == 480


def test_probe_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ProbeError):
        probe(tmp_path / "nope.mp4")


def test_probe_non_media_file_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.mp4"
    bogus.write_text("not a real media file")
    with pytest.raises(ProbeError):
        probe(bogus)


def test_probe_reports_rotated_phone_video_in_display_orientation(
    tmp_path: Path, make_video_clip
) -> None:
    # Phones store portrait video as landscape frames plus a rotation tag.
    stored = make_video_clip("stored_landscape.mp4", duration=1.0, fps=30, size="320x240")
    rotated = tmp_path / "phone_portrait.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-display_rotation", "90",
         "-i", str(stored), "-c", "copy", str(rotated)],
        check=True,
    )  # fmt: skip
    info = probe(rotated)
    assert (info.width, info.height) == (240, 320)


def test_probe_reports_non_square_pixels_at_display_width(tmp_path: Path) -> None:
    anamorphic = tmp_path / "anamorphic.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=duration=1:size=320x240:rate=30",
         "-vf", "setsar=4/3", "-c:v", "libx264", str(anamorphic)],
        check=True,
    )  # fmt: skip
    info = probe(anamorphic)
    assert (info.width, info.height) == (426, 240)  # 320 * 4/3, truncated to even
