import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QFileDialog, QMessageBox

from penultimate_cut.io.otio_io import save_project
from penultimate_cut.model.project import Clip, MediaRef, Project, Track
from penultimate_cut.ui.main_window import MainWindow


def make_window() -> MainWindow:
    return MainWindow(vo="null")


def pump_until(app, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def make_dirty(window: MainWindow) -> None:
    """Push a no-op command, just to flip the undo stack's clean bit --
    these tests care about dirty-tracking, not any particular edit."""
    window.undo_stack.push(QUndoCommand("test edit"))


def test_file_menu_has_expected_actions(qapp) -> None:
    window = make_window()
    try:
        file_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.menu() is not None and action.menu().title() == "&File"
        )
        labels = [action.text() for action in file_menu.actions() if action.text()]
        assert labels == ["New", "Open…", "Save", "Save As…", "Exit"]
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_window_title_shows_dirty_marker_only_after_an_edit(qapp) -> None:
    window = make_window()
    try:
        assert window.windowTitle() == "Untitled — Penultimate Cut"
        make_dirty(window)
        assert window.windowTitle() == "*Untitled — Penultimate Cut"
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_save_project_as_writes_file_and_clears_dirty_marker(qapp, tmp_path, monkeypatch) -> None:
    window = make_window()
    try:
        make_dirty(window)
        target = tmp_path / "my_project.otio"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))

        assert window._save_project_as() is True

        assert target.exists()
        assert window._current_path == target
        assert window.undo_stack.isClean()
        assert window.windowTitle() == "my_project.otio — Penultimate Cut"
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_save_project_as_adds_otio_suffix_if_missing(qapp, tmp_path, monkeypatch) -> None:
    window = make_window()
    try:
        target_no_suffix = tmp_path / "untitled_name"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **k: (str(target_no_suffix), "")
        )

        assert window._save_project_as() is True
        assert window._current_path == target_no_suffix.with_suffix(".otio")
        assert window._current_path.exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_save_project_without_a_path_behaves_like_save_as(qapp, tmp_path, monkeypatch) -> None:
    window = make_window()
    try:
        target = tmp_path / "first_save.otio"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))

        assert window._save_project() is True
        assert window._current_path == target
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_save_project_as_cancelled_dialog_leaves_state_unchanged(qapp, monkeypatch) -> None:
    window = make_window()
    try:
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        assert window._save_project_as() is False
        assert window._current_path is None
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_open_project_loads_clips_onto_the_timeline(
    qapp, tmp_path, make_video_clip, monkeypatch
) -> None:
    source = make_video_clip("fm_open.mp4", duration=3.0, fps=30, size="320x240")
    media = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(3),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=320,
        height=240,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    project_path = tmp_path / "existing.otio"

    save_project(project, project_path)

    window = make_window()
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(project_path), ""))

        window._open_project()

        assert len(window.project.tracks[0].items) == 1
        assert window.project.tracks[0].items[0].media_ref.path == source
        assert window._current_path == project_path
        assert window.undo_stack.isClean()
        assert window.windowTitle() == "existing.otio — Penultimate Cut"
        assert pump_until(qapp, lambda: len(window.media_bin.entries) == 1)
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_open_project_cancelled_dialog_leaves_current_project(qapp, monkeypatch) -> None:
    window = make_window()
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        original_project = window.project
        window._open_project()
        assert window.project is original_project
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_open_project_bad_file_shows_error_and_keeps_current_project(
    qapp, tmp_path, monkeypatch
) -> None:
    bad_path = tmp_path / "not_really_otio.otio"
    bad_path.write_text("this is not a valid otio file")

    window = make_window()
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad_path), ""))
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)

        original_project = window.project
        window._open_project()

        assert window.project is original_project
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_new_project_resets_state_and_clears_media_bin(qapp, make_video_clip) -> None:
    source = make_video_clip("fm_new.mp4", duration=1.0, fps=30, size="320x240")
    window = make_window()
    try:
        window.media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(window.media_bin.entries) == 1)

        window._current_path = Path("/tmp/whatever.otio")
        window._new_project()

        assert window._current_path is None
        assert window.project.tracks[0].items == []
        assert window.media_bin.entries == []
        assert window.windowTitle() == "Untitled — Penultimate Cut"
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_new_project_with_unsaved_changes_cancelled_keeps_project(qapp, monkeypatch) -> None:
    window = make_window()
    try:
        make_dirty(window)
        original_project = window.project
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )

        window._new_project()

        assert window.project is original_project
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_new_project_with_unsaved_changes_discard_resets_project(qapp, monkeypatch) -> None:
    window = make_window()
    try:
        make_dirty(window)
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Discard
        )

        window._new_project()

        assert window.undo_stack.isClean()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_close_with_unsaved_changes_cancelled_keeps_window_open(qapp, monkeypatch) -> None:
    window = make_window()
    try:
        window.show()
        qapp.processEvents()
        make_dirty(window)
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )

        window.close()
        qapp.processEvents()

        assert window.isVisible()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_offer_relink_applies_matches_by_filename(
    qapp, tmp_path, make_video_clip, monkeypatch
) -> None:
    source = make_video_clip("fm_relink.mp4", duration=2.0, fps=30, size="320x240")
    media = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(2),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=320,
        height=240,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    project_path = tmp_path / "with_media.otio"

    save_project(project, project_path)

    # Move the media file to simulate it going missing relative to the project.
    found_dir = tmp_path / "relocated"
    found_dir.mkdir()
    new_location = found_dir / source.name
    source.rename(new_location)

    window = make_window()
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(project_path), ""))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(found_dir))
        monkeypatch.setattr(
            QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok
        )

        window._open_project()

        relinked_clip = window.project.tracks[0].items[0]
        assert relinked_clip.media_ref.path == new_location
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_offer_relink_declined_leaves_media_missing(
    qapp, tmp_path, make_video_clip, monkeypatch
) -> None:
    source = make_video_clip("fm_relink_decline.mp4", duration=2.0, fps=30, size="320x240")
    media = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(2),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    project_path = tmp_path / "with_media2.otio"

    save_project(project, project_path)
    original_path = media.path
    source.unlink()

    window = make_window()
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(project_path), ""))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

        window._open_project()

        unrelinked_clip = window.project.tracks[0].items[0]
        assert unrelinked_clip.media_ref.path == original_path
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()
