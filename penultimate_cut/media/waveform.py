"""Audio waveform peaks, one pass per media file, cached on disk.

A file's whole audio track is reduced to a fixed number of (min, max) peak
pairs per second of audio, once. Every timeline clip cut from that file then
just reads its own slice (`peak_between`) -- so splitting or trimming a clip
never decodes anything again.

ffmpeg computes the peaks itself (`astats`, reset every bucket) and streams
them out as text, so memory stays small whatever the file's length. The
first version decoded raw samples into Python and took min/max there: about
85 s and 240 MB for a 2-hour file, nearly all of it Python looping over 57
million samples. This way the same file takes about 9 s.
"""

from __future__ import annotations

import subprocess
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from penultimate_cut.media.cache import cache_dir_for
from penultimate_cut.media.executables import NO_WINDOW, ExecutableNotFoundError, locate

PEAKS_PER_SECOND = 50
"""Detail of the stored waveform: one pixel per peak at the timeline's
default zoom (50 px/s)."""
_SAMPLE_RATE = 8000
_SAMPLES_PER_PEAK = _SAMPLE_RATE // PEAKS_PER_SECOND
_FULL_SCALE = 32768.0
_MIN_KEY = "lavfi.astats.Overall.Min_level="
_MAX_KEY = "lavfi.astats.Overall.Max_level="

Peak = tuple[float, float]
"""A (min, max) pair, each normalized to [-1.0, 1.0]."""


class WaveformError(RuntimeError):
    """ffmpeg couldn't decode audio from this file (or it has no audio track)."""


class WaveformCancelled(Exception):
    """`cancel_check` asked to stop before the waveform was finished."""


_COARSE_FACTOR = 16


@dataclass
class Waveform:
    """`lows[i]`/`highs[i]` are the peaks of the i-th 1/`peaks_per_second`
    slice of the file's audio.

    Also keeps a copy `_COARSE_FACTOR` times coarser, for spans covering
    many peaks: zoomed all the way out, each pixel of a long clip covers
    dozens of peaks, and reading them all made each repaint over 10x slower.
    """

    lows: array
    highs: array
    peaks_per_second: int = PEAKS_PER_SECOND
    _coarse: tuple[array, array] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        coarse_lows, coarse_highs = array("f"), array("f")
        for first in range(0, len(self.lows), _COARSE_FACTOR):
            coarse_lows.append(min(self.lows[first : first + _COARSE_FACTOR]))
            coarse_highs.append(max(self.highs[first : first + _COARSE_FACTOR]))
        self._coarse = (coarse_lows, coarse_highs)

    def __len__(self) -> int:
        return len(self.lows)


def media_waveform(media_path: Path, *, cancel_check: Callable[[], bool] | None = None) -> Waveform:
    """The waveform of `media_path`'s whole audio track, from the disk cache
    if it's there. Raises `WaveformCancelled` if `cancel_check()` turns true
    partway (ffmpeg is stopped and nothing is cached)."""
    cache_path = cache_dir_for(media_path) / f"waveform_{PEAKS_PER_SECOND}ps.bin"
    if cache_path.exists():
        return _read_cache(cache_path)
    waveform = _compute(media_path, cancel_check or (lambda: False))
    _write_cache(cache_path, waveform)
    return waveform


def peak_between(waveform: Waveform, start: float, end: float) -> Peak:
    """The (min, max) over source seconds `[start, end)` -- at least one
    peak's worth, so a span shorter than one peak still shows that peak.
    (0, 0) outside the audio."""
    rate: float = waveform.peaks_per_second
    lows, highs = waveform.lows, waveform.highs
    if (end - start) * rate >= 2 * _COARSE_FACTOR:
        lows, highs = waveform._coarse
        rate /= _COARSE_FACTOR
    first = max(0, int(start * rate))
    last = min(len(lows), max(first + 1, int(end * rate)))
    if first >= last:
        return (0.0, 0.0)
    return (min(lows[first:last]), max(highs[first:last]))


def _compute(media_path: Path, cancel_check: Callable[[], bool]) -> Waveform:
    try:
        ffmpeg = locate("ffmpeg")
    except ExecutableNotFoundError as exc:
        raise WaveformError(str(exc)) from exc

    audio_filter = ",".join(
        [
            f"aresample={_SAMPLE_RATE}",
            "aformat=sample_fmts=s16:channel_layouts=mono",
            f"asetnsamples=n={_SAMPLES_PER_PEAK}",
            "astats=metadata=1:reset=1:measure_overall=Min_level+Max_level:measure_perchannel=none",
            "ametadata=mode=print:file=-",
        ]
    )
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(media_path)]
    args += ["-vn", "-map", "0:a:0", "-af", audio_filter, "-f", "null", "-"]

    lows = array("f")
    highs = array("f")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=NO_WINDOW,
    )
    try:
        assert process.stdout is not None
        for count, line in enumerate(process.stdout):
            if line.startswith(_MIN_KEY):
                lows.append(float(line[len(_MIN_KEY) :]) / _FULL_SCALE)
            elif line.startswith(_MAX_KEY):
                highs.append(float(line[len(_MAX_KEY) :]) / _FULL_SCALE)
            if count % 3000 == 0 and cancel_check():
                raise WaveformCancelled()
        stderr = process.stderr.read() if process.stderr else ""
        if process.wait() != 0:
            raise WaveformError(f"{media_path}: ffmpeg failed: {stderr.strip()}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if not lows or len(lows) != len(highs):
        raise WaveformError(f"{media_path}: no audio to draw")
    return Waveform(lows, highs)


def _write_cache(cache_path: Path, waveform: Waveform) -> None:
    flat = array("f")
    for lo, hi in zip(waveform.lows, waveform.highs, strict=True):
        flat.append(lo)
        flat.append(hi)
    # Write-then-rename, so an interrupted write never leaves a short file
    # that a later run would read as the whole waveform.
    partial = cache_path.with_suffix(".partial")
    partial.write_bytes(flat.tobytes())
    partial.replace(cache_path)


def _read_cache(cache_path: Path) -> Waveform:
    flat = array("f")
    flat.frombytes(cache_path.read_bytes())
    return Waveform(array("f", flat[0::2]), array("f", flat[1::2]))
