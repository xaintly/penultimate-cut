from pathlib import Path

import pytest

from penultimate_cut.media.cache import cache_dir_for
from penultimate_cut.media.probe import probe
from penultimate_cut.media.waveform import WaveformError, peaks


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache_root"))


def test_peaks_returns_requested_count_within_range(make_video_clip) -> None:
    path = make_video_clip("waveform_basic.mp4", duration=2.0, fps=30, size="320x240")
    info = probe(path)

    result = peaks(path, info.duration, count=20)

    assert len(result) == 20
    for lo, hi in result:
        assert -1.0 <= lo <= hi <= 1.0


def test_peaks_detects_signal_on_a_sine_tone(make_audio_clip) -> None:
    # A sine wave should produce real (non-silent) peaks somewhere in the clip.
    path = make_audio_clip("waveform_tone.m4a", duration=2.0)
    info = probe(path)

    result = peaks(path, info.duration, count=10)

    assert any(hi - lo > 0.01 for lo, hi in result)


def test_peaks_does_not_regenerate_cached_result(make_video_clip) -> None:
    path = make_video_clip("waveform_cache.mp4", duration=2.0, fps=30, size="320x240")
    info = probe(path)

    peaks(path, info.duration, count=8)
    cache_file = cache_dir_for(path) / "peaks_8_8000sr.bin"
    assert cache_file.exists()
    sentinel = b"\x00" * cache_file.stat().st_size
    cache_file.write_bytes(sentinel)

    second = peaks(path, info.duration, count=8)

    assert second == [(0.0, 0.0)] * 8


def test_peaks_raises_when_media_has_no_audio(make_silent_video_clip) -> None:
    path = make_silent_video_clip("waveform_silent.mp4", duration=1.0, size="320x240")
    info = probe(path)

    with pytest.raises(WaveformError):
        peaks(path, info.duration, count=10)


def test_peaks_rejects_non_positive_count(make_audio_clip) -> None:
    path = make_audio_clip("waveform_badcount.m4a", duration=1.0)
    info = probe(path)
    with pytest.raises(ValueError):
        peaks(path, info.duration, count=0)
