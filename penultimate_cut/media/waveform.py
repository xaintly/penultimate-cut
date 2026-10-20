"""Audio waveform peaks, cached on disk.

ffmpeg decodes and resamples the audio track to a fixed, modest sample rate
(exact pitch/frequency content doesn't matter for a visual waveform, and
resampling first keeps memory bounded for long clips) before peaks are
computed in Python.
"""

from __future__ import annotations

import subprocess
from array import array
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.cache import cache_dir_for
from penultimate_cut.media.executables import ExecutableNotFoundError, locate

DEFAULT_SAMPLE_RATE = 8000
Peak = tuple[float, float]
"""A (min, max) pair, each normalized to [-1.0, 1.0]."""


class WaveformError(RuntimeError):
    """ffmpeg couldn't decode audio from this file (or it has no audio track)."""


def peaks(
    media_path: Path,
    duration: Fraction,
    count: int,
    *,
    start: Fraction = Fraction(0),
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> list[Peak]:
    """Return `count` (min, max) peak pairs spanning `[start, start+duration)`
    of the audio track, cached on disk.

    `start` defaults to 0 (the whole file); a trimmed timeline clip passes
    its own `source_in` so the waveform reflects what the clip actually
    plays, not the start of the source file. Seeking to `start` uses
    ffmpeg's fast (keyframe-approximate) seek, not the frame-exact kind --
    fine here, since this is a visual approximation, never exported content.
    """
    if count < 1:
        raise ValueError("count must be at least 1")

    cache_dir = cache_dir_for(media_path)
    start_us = int(start * 1_000_000)
    duration_us = int(duration * 1_000_000)
    cache_path = cache_dir / f"peaks_{count}_{sample_rate}sr_s{start_us}_d{duration_us}.bin"
    if cache_path.exists():
        return _read_cache(cache_path, count)

    samples = _decode_pcm(media_path, sample_rate, start, duration)
    result = _bucket_peaks(samples, count)
    _write_cache(cache_path, result)
    return result


def _bucket_peaks(samples: array, count: int) -> list[Peak]:
    total = len(samples)
    if total == 0:
        return [(0.0, 0.0)] * count

    bucket_size = total / count
    result: list[Peak] = []
    for index in range(count):
        start = int(index * bucket_size)
        end = max(start + 1, int((index + 1) * bucket_size))
        chunk = samples[start:end]
        if not chunk:
            result.append((0.0, 0.0))
            continue
        result.append((min(chunk) / 32768.0, max(chunk) / 32768.0))
    return result


def _decode_pcm(media_path: Path, sample_rate: int, start: Fraction, duration: Fraction) -> array:
    try:
        ffmpeg = locate("ffmpeg")
    except ExecutableNotFoundError as exc:
        raise WaveformError(str(exc)) from exc

    args = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", str(float(start))]
    args += ["-i", str(media_path), "-t", str(float(duration))]
    args += ["-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"]

    try:
        result = subprocess.run(args, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        raise WaveformError(f"{media_path}: ffmpeg failed: {stderr}") from exc

    samples = array("h")
    samples.frombytes(result.stdout)
    return samples


def _write_cache(cache_path: Path, result: list[Peak]) -> None:
    flat = array("f")
    for lo, hi in result:
        flat.append(lo)
        flat.append(hi)
    cache_path.write_bytes(flat.tobytes())


def _read_cache(cache_path: Path, count: int) -> list[Peak]:
    flat = array("f")
    flat.frombytes(cache_path.read_bytes())
    return [(flat[2 * i], flat[2 * i + 1]) for i in range(count)]
