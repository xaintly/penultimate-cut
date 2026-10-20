from fractions import Fraction

import pytest

from penultimate_cut.export.encoders import (
    EncoderError,
    audio_bitrate,
    encoder_args,
    estimate_file_size,
    video_bitrate,
)


def test_video_bitrate_scales_with_resolution_and_quality() -> None:
    small = video_bitrate("small_file", 1280, 720, Fraction(30))
    balanced = video_bitrate("balanced", 1280, 720, Fraction(30))
    high = video_bitrate("high_quality", 1280, 720, Fraction(30))
    assert small < balanced < high

    sd = video_bitrate("balanced", 640, 480, Fraction(30))
    hd = video_bitrate("balanced", 1920, 1080, Fraction(30))
    assert sd < hd


def test_audio_bitrate_scales_with_quality() -> None:
    assert audio_bitrate("small_file") < audio_bitrate("balanced") < audio_bitrate("high_quality")


def test_estimate_file_size_is_exact_arithmetic_on_bitrates() -> None:
    width, height, rate, duration = 1280, 720, Fraction(30), Fraction(10)
    expected = round(
        (video_bitrate("balanced", width, height, rate) + audio_bitrate("balanced")) * 10 / 8
    )
    assert estimate_file_size("balanced", width, height, rate, duration) == expected


def test_estimate_file_size_scales_linearly_with_duration() -> None:
    args = ("balanced", 1280, 720, Fraction(30))
    size_10s = estimate_file_size(*args, Fraction(10))
    size_20s = estimate_file_size(*args, Fraction(20))
    assert size_20s == pytest.approx(size_10s * 2, rel=0.01)


def test_libx264_args_contain_bitrate_and_fixed_output_settings() -> None:
    args = encoder_args("libx264", "balanced", 1280, 720, Fraction(30))
    joined = " ".join(args)
    assert "-c:v libx264" in joined
    assert "-pix_fmt yuv420p" in joined
    assert "-c:a aac" in joined
    assert "-ar 48000" in joined
    assert "-ac 2" in joined
    assert "-movflags +faststart" in joined
    bitrate = video_bitrate("balanced", 1280, 720, Fraction(30))
    assert f"-b:v {bitrate}" in joined


def test_h264_mf_args_contain_fixed_output_settings() -> None:
    # Windows-only encoder; can only check the args shape here, not run it.
    args = encoder_args("h264_mf", "balanced", 1280, 720, Fraction(30))
    joined = " ".join(args)
    assert "-c:v h264_mf" in joined
    assert "-pix_fmt yuv420p" in joined
    assert "-movflags +faststart" in joined


def test_unknown_encoder_raises() -> None:
    with pytest.raises(EncoderError):
        encoder_args("vp9", "balanced", 1280, 720, Fraction(30))  # type: ignore[arg-type]
