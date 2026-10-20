import time
from fractions import Fraction
from pathlib import Path

from penultimate_cut.io.autosave import (
    autosave_path_for,
    discard_autosave,
    find_recoverable_autosave,
    load_autosave,
    write_autosave,
)
from penultimate_cut.model.project import Clip, MediaRef, Project, Track


def make_project(tmp_path: Path) -> Project:
    media = MediaRef(
        path=tmp_path / "a.mp4",
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])


def test_autosave_path_for_a_saved_project_is_a_sibling_file(tmp_path: Path) -> None:
    project_path = tmp_path / "my_video.otio"
    assert autosave_path_for(project_path) == tmp_path / "my_video.otio.autosave"


def test_autosave_path_for_an_unsaved_project_uses_a_fixed_slot(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_AUTOSAVE_DIR", str(tmp_path / "autosave_root"))
    assert autosave_path_for(None) == tmp_path / "autosave_root" / "Untitled.otio.autosave"


def test_write_then_load_autosave_round_trips(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    project_path = tmp_path / "proj.otio"

    write_autosave(project, project_path)

    autosave_path = autosave_path_for(project_path)
    assert autosave_path.exists()
    loaded = load_autosave(autosave_path).project
    assert len(loaded.tracks[0].items) == 1
    assert loaded.tracks[0].items[0].source_out == Fraction(5)


def test_find_recoverable_autosave_absent_returns_none(tmp_path: Path) -> None:
    assert find_recoverable_autosave(tmp_path / "proj.otio") is None


def test_find_recoverable_autosave_newer_than_project_is_returned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_AUTOSAVE_DIR", str(tmp_path / "autosave_root"))
    project = make_project(tmp_path)
    project_path = tmp_path / "proj.otio"
    from penultimate_cut.io.otio_io import save_project

    save_project(project, project_path)

    # Make sure the autosave's mtime is unambiguously later.
    time.sleep(0.02)
    write_autosave(project, project_path)

    found = find_recoverable_autosave(project_path)
    assert found == autosave_path_for(project_path)


def test_find_recoverable_autosave_older_than_project_is_not_returned(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    project_path = tmp_path / "proj.otio"
    from penultimate_cut.io.otio_io import save_project

    write_autosave(project, project_path)
    time.sleep(0.02)
    save_project(project, project_path)  # project saved again, now newer than the autosave

    assert find_recoverable_autosave(project_path) is None


def test_find_recoverable_autosave_for_never_saved_project_needs_no_project_file(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_AUTOSAVE_DIR", str(tmp_path / "autosave_root"))
    project = make_project(tmp_path)
    write_autosave(project, None)

    assert find_recoverable_autosave(None) == autosave_path_for(None)


def test_discard_autosave_removes_the_file(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    project_path = tmp_path / "proj.otio"
    write_autosave(project, project_path)
    assert autosave_path_for(project_path).exists()

    discard_autosave(project_path)

    assert not autosave_path_for(project_path).exists()


def test_discard_autosave_is_a_noop_when_nothing_to_discard(tmp_path: Path) -> None:
    discard_autosave(tmp_path / "never_existed.otio")  # must not raise


def test_autosave_root_respects_xdg_data_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PENULTIMATE_CUT_AUTOSAVE_DIR", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))

    assert autosave_path_for(None) == tmp_path / "xdg_data" / "penultimate_cut" / "autosave" / (
        "Untitled.otio.autosave"
    )
