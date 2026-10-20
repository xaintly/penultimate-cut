"""Encoder selection and quality presets.

Kept behind this one function so the encoder can be swapped without
touching anything else (see CLAUDE.md's Licensing section): `libx264` is
the default (bundled, GPL, best quality-per-byte), `h264_mf` (Windows'
built-in Media Foundation encoder) is a fallback option.

`h264_mf` is Windows-only and can't be exercised at all on this (Linux) dev
machine -- its args below are written from ffmpeg's documented options, not
verified against a real run. Test on Windows before shipping it as a real
user-facing choice.

Quality tiers target an average video bitrate (bits-per-pixel * width *
height * frame_rate) rather than a quality factor like CRF, even though CRF
usually gets better quality-per-byte at a given file size. The export
dialog has to show an estimated file size *before* encoding starts
(CLAUDE.md's Export section), which only a bitrate target makes directly
computable -- `estimate_file_size` below is exact arithmetic on the same
numbers passed to ffmpeg, not a guess.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

VideoQuality = Literal["small_file", "balanced", "high_quality"]
EncoderName = Literal["libx264", "h264_mf"]

DEFAULT_ENCODER: EncoderName = "libx264"

_BITS_PER_PIXEL: dict[VideoQuality, float] = {
    "small_file": 0.04,
    "balanced": 0.08,
    "high_quality": 0.15,
}
_AUDIO_BITRATE_BPS: dict[VideoQuality, int] = {
    "small_file": 96_000,
    "balanced": 128_000,
    "high_quality": 192_000,
}
_OUTPUT_SAMPLE_RATE = 48_000
_OUTPUT_CHANNELS = 2


class EncoderError(ValueError):
    pass


def video_bitrate(quality: VideoQuality, width: int, height: int, frame_rate: Fraction) -> int:
    """Average video bitrate, in bits/second, for `quality` at this resolution/rate."""
    return round(_BITS_PER_PIXEL[quality] * width * height * float(frame_rate))


def audio_bitrate(quality: VideoQuality) -> int:
    """Audio bitrate, in bits/second, for `quality`."""
    return _AUDIO_BITRATE_BPS[quality]


def estimate_file_size(
    quality: VideoQuality, width: int, height: int, frame_rate: Fraction, duration: Fraction
) -> int:
    """Estimated output file size in bytes. An estimate, not a guarantee --
    single-pass average-bitrate encoding doesn't hit the target exactly."""
    total_bps = video_bitrate(quality, width, height, frame_rate) + audio_bitrate(quality)
    return round(total_bps * float(duration) / 8)


def encoder_args(
    encoder: EncoderName, quality: VideoQuality, width: int, height: int, frame_rate: Fraction
) -> list[str]:
    """Full set of ffmpeg args for the fixed output format: H.264 (yuv420p,
    +faststart) video at `quality`'s bitrate, AAC (stereo, 48 kHz) audio."""
    if encoder == "libx264":
        video = _libx264_args(quality, width, height, frame_rate)
    elif encoder == "h264_mf":
        video = _h264_mf_args(quality, width, height, frame_rate)
    else:
        raise EncoderError(f"unknown encoder: {encoder}")

    bitrate = audio_bitrate(quality)
    audio = [
        "-c:a",
        "aac",
        "-b:a",
        f"{bitrate}",
        "-ar",
        str(_OUTPUT_SAMPLE_RATE),
        "-ac",
        str(_OUTPUT_CHANNELS),
    ]
    return [*video, *audio, "-movflags", "+faststart"]


def _libx264_args(
    quality: VideoQuality, width: int, height: int, frame_rate: Fraction
) -> list[str]:
    bitrate = video_bitrate(quality, width, height, frame_rate)
    return [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        f"{bitrate}",
        "-maxrate",
        f"{round(bitrate * 1.5)}",
        "-bufsize",
        f"{bitrate * 2}",
    ]


def _h264_mf_args(
    quality: VideoQuality, width: int, height: int, frame_rate: Fraction
) -> list[str]:
    bitrate = video_bitrate(quality, width, height, frame_rate)
    return [
        "-c:v",
        "h264_mf",
        "-rate_control",
        "vbr",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        f"{bitrate}",
    ]
