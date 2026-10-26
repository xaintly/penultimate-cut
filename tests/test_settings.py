from pathlib import Path

from penultimate_cut.ui.settings import (
    DEFAULT_PREVIEW_VOLUME,
    default_folder,
    load_preview_volume,
    save_preview_volume,
    unique_path,
)


def test_default_folder_is_created(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "Videos" / "PenultimateCut"
    monkeypatch.setenv("PENULTIMATE_CUT_DEFAULT_DIR", str(target))
    assert default_folder() == target
    assert target.is_dir()


def test_default_folder_is_under_videos_without_override(monkeypatch, qapp) -> None:
    monkeypatch.delenv("PENULTIMATE_CUT_DEFAULT_DIR")
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)  # don't touch the real Videos
    folder = default_folder()
    assert folder.name == "PenultimateCut"
    assert folder.parent.name == "Videos"


def test_unique_path_skips_existing_files(tmp_path: Path) -> None:
    first = tmp_path / "Untitled.mp4"
    assert unique_path(first) == first
    first.touch()
    assert unique_path(first) == tmp_path / "Untitled (2).mp4"
    (tmp_path / "Untitled (2).mp4").touch()
    assert unique_path(first) == tmp_path / "Untitled (3).mp4"


def test_preview_volume_round_trips(qapp) -> None:
    try:
        save_preview_volume(25)
        assert load_preview_volume() == 25
    finally:
        save_preview_volume(DEFAULT_PREVIEW_VOLUME)
