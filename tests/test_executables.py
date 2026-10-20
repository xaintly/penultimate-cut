import sys
from pathlib import Path

import pytest

from penultimate_cut.media.executables import ExecutableNotFoundError, locate, vendor_dir


def test_vendor_dir_in_dev_mode_is_repo_root_vendor(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert vendor_dir() == Path(__file__).resolve().parents[1] / "vendor"


def test_vendor_dir_when_frozen_is_next_to_the_executable(monkeypatch, tmp_path) -> None:
    fake_exe = tmp_path / "PenultimateCut.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert vendor_dir() == tmp_path / "vendor"


def test_locate_prefers_a_vendored_executable(monkeypatch, tmp_path) -> None:
    vendored = tmp_path / "ffmpeg"
    vendored.touch()
    monkeypatch.setattr("penultimate_cut.media.executables.vendor_dir", lambda: tmp_path)
    monkeypatch.setattr("penultimate_cut.media.executables.platform.system", lambda: "Linux")

    assert locate("ffmpeg") == str(vendored)


def test_locate_adds_exe_suffix_on_windows(monkeypatch, tmp_path) -> None:
    vendored = tmp_path / "ffmpeg.exe"
    vendored.touch()
    monkeypatch.setattr("penultimate_cut.media.executables.vendor_dir", lambda: tmp_path)
    monkeypatch.setattr("penultimate_cut.media.executables.platform.system", lambda: "Windows")

    assert locate("ffmpeg") == str(vendored)


def test_locate_falls_back_to_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("penultimate_cut.media.executables.vendor_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "penultimate_cut.media.executables.shutil.which", lambda name: "/usr/bin/ffmpeg"
    )

    assert locate("ffmpeg") == "/usr/bin/ffmpeg"


def test_locate_raises_when_not_found_anywhere(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("penultimate_cut.media.executables.vendor_dir", lambda: tmp_path)
    monkeypatch.setattr("penultimate_cut.media.executables.shutil.which", lambda name: None)

    with pytest.raises(ExecutableNotFoundError):
        locate("ffmpeg")
