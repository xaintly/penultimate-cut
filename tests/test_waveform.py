import subprocess
from array import array
from pathlib import Path

import pytest

from penultimate_cut.media.cache import cache_dir_for
from penultimate_cut.media.waveform import (
    PEAKS_PER_SECOND,
    Waveform,
    WaveformCancelled,
    WaveformError,
    media_waveform,
    peak_between,
)


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache_root"))


def test_waveform_covers_the_whole_file_at_a_fixed_rate(make_video_clip) -> None:
    path = make_video_clip("waveform_basic.mp4", duration=2.0, fps=30, size="320x240")

    waveform = media_waveform(path)

    # 2 s of audio; AAC padding can add a peak or two at the end.
    assert abs(len(waveform) - 2 * PEAKS_PER_SECOND) <= 3
    for lo, hi in zip(waveform.lows, waveform.highs, strict=True):
        assert -1.0 <= lo <= hi <= 1.0


def test_waveform_detects_signal_on_a_sine_tone(make_audio_clip) -> None:
    path = make_audio_clip("waveform_tone.m4a", duration=2.0)

    lo, hi = peak_between(media_waveform(path), 0.5, 1.5)

    assert hi - lo > 0.01


def test_waveform_is_cached_on_disk(make_video_clip) -> None:
    path = make_video_clip("waveform_cache.mp4", duration=2.0, fps=30, size="320x240")
    media_waveform(path)
    cache_file = cache_dir_for(path) / f"waveform_{PEAKS_PER_SECOND}ps.bin"
    assert cache_file.exists()
    cache_file.write_bytes(array("f", [0.0] * 8).tobytes())  # 4 silent peaks

    again = media_waveform(path)

    assert len(again) == 4
    assert max(again.highs) == 0.0


def test_peak_between_reads_the_right_window(tmp_path: Path) -> None:
    # Silence for the first second, a real tone for the second.
    path = tmp_path / "half_silent.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:r=44100:d=1",
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    waveform = media_waveform(path)

    silent_lo, silent_hi = peak_between(waveform, 0.1, 0.9)
    tone_lo, tone_hi = peak_between(waveform, 1.1, 1.9)

    assert silent_hi - silent_lo < 0.01
    assert tone_hi - tone_lo > 0.1


def test_peak_between_handles_tiny_and_out_of_range_spans() -> None:
    waveform = Waveform(array("f", [-0.5, -0.25]), array("f", [0.5, 0.25]), peaks_per_second=1)

    assert peak_between(waveform, 0.0, 0.001) == (-0.5, 0.5)  # less than one peak
    assert peak_between(waveform, 0.0, 2.0) == (-0.5, 0.5)
    assert peak_between(waveform, 1.5, 1.6) == (-0.25, 0.25)
    assert peak_between(waveform, 5.0, 6.0) == (0.0, 0.0)


def test_waveform_raises_when_media_has_no_audio(make_silent_video_clip) -> None:
    path = make_silent_video_clip("waveform_silent.mp4", duration=1.0, size="320x240")

    with pytest.raises(WaveformError):
        media_waveform(path)


def test_cancelled_waveform_is_not_cached(make_audio_clip) -> None:
    path = make_audio_clip("waveform_cancel.m4a", duration=2.0)

    with pytest.raises(WaveformCancelled):
        media_waveform(path, cancel_check=lambda: True)

    assert not (cache_dir_for(path) / f"waveform_{PEAKS_PER_SECOND}ps.bin").exists()


def test_peak_between_long_spans_use_the_coarse_copy_without_losing_peaks() -> None:
    lows = array("f", [0.0] * 1000)
    highs = array("f", [0.0] * 1000)
    highs[517] = 0.75  # one loud peak in the middle of a long span
    waveform = Waveform(lows, highs, peaks_per_second=10)

    assert peak_between(waveform, 40.0, 60.0) == (0.0, 0.75)
    assert peak_between(waveform, 0.0, 40.0) == (0.0, 0.0)
