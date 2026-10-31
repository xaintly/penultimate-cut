import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from penultimate_cut.io.autosave import autosave_path_for, unsaved_projects, write_autosave
from penultimate_cut.io.otio_io import save_project
from penultimate_cut.model.project import Clip, MediaRef, Project, Track
from penultimate_cut.ui.export_dialog import ExportDialog
from penultimate_cut.ui.main_window import MainWindow


def isolate_autosave_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_AUTOSAVE_DIR", str(tmp_path / "autosave_root"))


def make_project(source: Path, duration: float = 2.0) -> Project:
    media = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(str(duration)),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=320,
        height=240,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(str(duration)))
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])


def test_autosave_timer_is_configured(qapp, monkeypatch, tmp_path) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    window = MainWindow(vo="null")
    try:
        assert window._autosave_timer.isActive()
        assert window._autosave_timer.interval() == 60_000
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_autosave_skips_writing_when_clean(qapp, monkeypatch, tmp_path) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    window = MainWindow(vo="null")
    try:
        window._autosave()
        assert not autosave_path_for(None).exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_autosave_writes_when_dirty(qapp, monkeypatch, tmp_path, make_video_clip) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_dirty.mp4", duration=1.0, fps=30, size="320x240")
    window = MainWindow(vo="null")
    try:
        window.timeline.insert_clip(make_project(source).tracks[0].items[0])
        assert not window.undo_stack.isClean()

        window._autosave()

        assert autosave_path_for(None).exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_export_start_triggers_an_autosave(qapp, monkeypatch, tmp_path, make_video_clip) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_export.mp4", duration=1.0, fps=30, size="320x240")
    window = MainWindow(vo="null")
    monkeypatch.setattr(ExportDialog, "exec", lambda self: None)
    try:
        window.timeline.insert_clip(make_project(source).tracks[0].items[0])

        window._open_export_dialog()

        assert autosave_path_for(None).exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_discarding_unsaved_changes_removes_the_autosave(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_discard.mp4", duration=1.0, fps=30, size="320x240")
    window = MainWindow(vo="null")
    try:
        window.timeline.insert_clip(make_project(source).tracks[0].items[0])
        window._autosave()
        assert autosave_path_for(None).exists()

        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Discard
        )
        assert window._confirm_discard_if_dirty() is True

        assert not autosave_path_for(None).exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_saving_removes_the_old_autosave(qapp, monkeypatch, tmp_path, make_video_clip) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_save.mp4", duration=1.0, fps=30, size="320x240")
    window = MainWindow(vo="null")
    try:
        window.timeline.insert_clip(make_project(source).tracks[0].items[0])
        window._autosave()
        assert autosave_path_for(None).exists()

        target = tmp_path / "saved.otio"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
        assert window._save_project_as() is True

        assert not autosave_path_for(None).exists()
        assert not autosave_path_for(target).exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_launch_offers_to_recover_a_never_saved_autosave(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_launch_recover.mp4", duration=1.0, fps=30, size="320x240")
    write_autosave(make_project(source), None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window = MainWindow(vo="null")
    try:
        assert len(window.project.tracks[0].items) == 1
        assert window.project.tracks[0].items[0].media_ref.path == source
        assert window._current_path is None
        assert not window.undo_stack.isClean()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_launch_declining_recovery_discards_the_autosave(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_launch_decline.mp4", duration=1.0, fps=30, size="320x240")
    write_autosave(make_project(source), None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    window = MainWindow(vo="null")
    try:
        assert window.project.tracks[0].items == []
        assert not autosave_path_for(None).exists()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_opening_a_project_offers_its_own_newer_autosave(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    original_source = make_video_clip("asw_open_orig.mp4", duration=1.0, fps=30, size="320x240")
    newer_source = make_video_clip("asw_open_newer.mp4", duration=1.0, fps=30, size="320x240")
    project_path = tmp_path / "proj.otio"
    save_project(make_project(original_source), project_path)
    time.sleep(0.02)  # guarantee a distinct, later mtime for the autosave
    # Written directly, not via write_autosave(): an autosave the launch check
    # doesn't know about -- from an older version of the app, say, or one
    # that wasn't the most recent of several left unsaved.
    save_project(make_project(newer_source), autosave_path_for(project_path))

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(project_path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window = MainWindow(vo="null")
    try:
        window._open_project()

        assert window.project.tracks[0].items[0].media_ref.path == newer_source
        assert window._current_path == project_path
        assert not window.undo_stack.isClean()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_launch_after_a_crash_offers_the_saved_project_left_unsaved(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    """A saved project's autosave is beside it, not in the autosave folder;
    the launch still finds it, through the list of unsaved projects."""
    isolate_autosave_dir(monkeypatch, tmp_path)
    original_source = make_video_clip("asw_crash_orig.mp4", duration=1.0, fps=30, size="320x240")
    newer_source = make_video_clip("asw_crash_newer.mp4", duration=1.0, fps=30, size="320x240")
    project_path = tmp_path / "crashed.otio"
    save_project(make_project(original_source), project_path)
    time.sleep(0.02)
    write_autosave(make_project(newer_source), project_path)  # ...and then the crash
    assert unsaved_projects() == [project_path]

    asked: list[str] = []

    def answer(_parent, _title, text, *_a, **_k):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    window = MainWindow(vo="null")
    try:
        assert len(asked) == 1 and "“crashed”" in asked[0]
        assert window.project.tracks[0].items[0].media_ref.path == newer_source
        assert window._current_path == project_path
        assert not window.undo_stack.isClean()
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_launch_declining_the_saved_projects_recovery_forgets_it(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_crash_decline.mp4", duration=1.0, fps=30, size="320x240")
    project_path = tmp_path / "declined.otio"
    save_project(make_project(source), project_path)
    time.sleep(0.02)
    write_autosave(make_project(source), project_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    window = MainWindow(vo="null")
    try:
        assert window._current_path is None
        assert not autosave_path_for(project_path).exists()
        assert unsaved_projects() == []
    finally:
        window.close()
        qapp.processEvents()


def test_an_edit_is_autosaved_once_editing_pauses(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_pause.mp4", duration=1.0, fps=30, size="320x240")
    window = MainWindow(vo="null")
    try:
        window._edit_autosave_timer.setInterval(50)
        window.timeline.insert_clip(make_project(source).tracks[0].items[0])
        assert window._edit_autosave_timer.isActive()
        assert pump_until(qapp, lambda: autosave_path_for(None).exists())
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_a_normal_exit_leaves_nothing_to_recover(
    qapp, monkeypatch, tmp_path, make_video_clip
) -> None:
    """Saved, discarded, or undone back to where it was saved: either way
    the next launch has nothing to offer."""
    isolate_autosave_dir(monkeypatch, tmp_path)
    source = make_video_clip("asw_exit.mp4", duration=1.0, fps=30, size="320x240")
    project_path = tmp_path / "exit.otio"
    save_project(make_project(source), project_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(project_path), ""))

    window = MainWindow(vo="null")
    try:
        window._open_project()
        window.timeline.insert_clip(make_project(source).tracks[0].items[0])
        window._autosave()
        assert autosave_path_for(project_path).exists()
        assert unsaved_projects() == [project_path]

        window.undo_stack.undo()  # back to the saved state
        assert window.undo_stack.isClean()
    finally:
        window.close()
        qapp.processEvents()

    assert not autosave_path_for(project_path).exists()
    assert unsaved_projects() == []


def pump_until(app, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False
