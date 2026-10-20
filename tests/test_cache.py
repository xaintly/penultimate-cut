from pathlib import Path

import pytest

from penultimate_cut.media.cache import cache_dir_for


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache_root = tmp_path / "cache_root"
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(cache_root))
    return cache_root


def test_cache_dir_is_created_and_stable(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"hello")

    first = cache_dir_for(media)
    second = cache_dir_for(media)

    assert first == second
    assert first.is_dir()


def test_cache_dir_changes_when_file_is_replaced(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"hello")
    before = cache_dir_for(media)

    media.write_bytes(b"a completely different, longer payload")
    after = cache_dir_for(media)

    assert before != after


def test_different_files_get_different_cache_dirs(tmp_path: Path) -> None:
    media_a = tmp_path / "a.mp4"
    media_b = tmp_path / "b.mp4"
    media_a.write_bytes(b"hello")
    media_b.write_bytes(b"hello")

    assert cache_dir_for(media_a) != cache_dir_for(media_b)
