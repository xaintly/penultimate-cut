import subprocess
import time
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.probe import probe
from penultimate_cut.model.project import Clip, MediaRef, Project, Title, Track, new_title_clip
from penultimate_cut.ui.export_dialog import ExportDialog


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


def pump_until(app, condition, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_initial_estimate_is_shown(qapp, make_video_clip) -> None:
    source = make_video_clip("ed_initial.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source)
    dialog = ExportDialog(project)
    assert dialog._size_label.text().startswith("~")
    assert dialog._size_label.text() != "~"


def test_estimate_changes_with_quality(qapp, make_video_clip) -> None:
    source = make_video_clip("ed_quality.mp4", duration=10.0, fps=30, size="320x240")
    project = make_project(source, duration=10.0)
    dialog = ExportDialog(project)

    small_index = dialog._quality_combo.findData("small_file")
    dialog._quality_combo.setCurrentIndex(small_index)
    small_text = dialog._size_label.text()

    high_index = dialog._quality_combo.findData("high_quality")
    dialog._quality_combo.setCurrentIndex(high_index)
    high_text = dialog._size_label.text()

    assert small_text != high_text


def test_estimate_works_for_an_empty_project(qapp) -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    dialog = ExportDialog(project)
    assert dialog._size_label.text().startswith("~")


def test_browse_button_sets_path(qapp, make_video_clip, monkeypatch, tmp_path) -> None:
    source = make_video_clip("ed_browse.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source)
    dialog = ExportDialog(project)

    chosen = str(tmp_path / "chosen.mp4")
    monkeypatch.setattr(
        "penultimate_cut.ui.export_dialog.QFileDialog.getSaveFileName",
        lambda *a, **k: (chosen, "MP4 video (*.mp4)"),
    )
    dialog._choose_path()

    assert dialog._path_edit.text() == chosen


def test_cancel_before_export_rejects_dialog(qapp, make_video_clip) -> None:
    source = make_video_clip("ed_cancel_before.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source)
    dialog = ExportDialog(project)

    result = []
    dialog.rejected.connect(lambda: result.append("rejected"))
    dialog._on_cancel_clicked()

    assert result == ["rejected"]


def test_export_fails_gracefully_on_empty_project(qapp) -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    dialog = ExportDialog(project)
    dialog._start_export()

    assert "Can't export" in dialog._status_label.text()
    assert not dialog._exporting


def test_real_export_succeeds_and_closes_dialog(qapp, make_video_clip, tmp_path) -> None:
    source = make_video_clip("ed_real.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)
    dialog = ExportDialog(project)
    dialog._path_edit.setText(str(tmp_path / "output"))  # no extension, on purpose

    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog._start_export()

    assert pump_until(qapp, lambda: accepted)
    assert dialog._status_label.text() == "Done."

    output_path = tmp_path / "output.mp4"
    assert output_path.exists()
    info = probe(output_path)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (320, 240)
    assert abs(info.duration - Fraction(2)) < Fraction(1, 5)


def test_cancel_during_export_deletes_partial_file(qapp, make_video_clip, tmp_path) -> None:
    source = make_video_clip("ed_cancel_during.mp4", duration=30.0, fps=30, size="320x240")
    project = make_project(source, duration=30.0)
    dialog = ExportDialog(project)
    output_path = tmp_path / "cancelme.mp4"
    dialog._path_edit.setText(str(output_path))

    progressed = []
    dialog._signals.progress.connect(lambda p: progressed.append(p))
    dialog._start_export()

    assert pump_until(qapp, lambda: len(progressed) > 0)
    dialog._on_cancel_clicked()

    assert pump_until(qapp, lambda: dialog._status_label.text() == "Export cancelled.")
    assert not dialog._exporting
    assert not output_path.exists()


def test_disables_controls_while_exporting(qapp, make_video_clip, tmp_path) -> None:
    source = make_video_clip("ed_disable.mp4", duration=15.0, fps=30, size="320x240")
    project = make_project(source, duration=15.0)
    dialog = ExportDialog(project)
    dialog.show()  # isVisible() below reflects real on-screen state, not just the internal flag
    qapp.processEvents()
    dialog._path_edit.setText(str(tmp_path / "disable_test.mp4"))

    dialog._start_export()
    try:
        assert not dialog._resolution_combo.isEnabled()
        assert not dialog._quality_combo.isEnabled()
        assert not dialog._export_button.isEnabled()
        assert dialog._progress_bar.isVisible()
    finally:
        dialog._on_cancel_clicked()
        assert pump_until(qapp, lambda: not dialog._exporting)


def test_failed_export_stays_visible_and_offers_details(
    qapp, tmp_path, make_video_clip, monkeypatch
) -> None:
    source = make_video_clip("ed_fail.mp4", duration=1.0, fps=30, size="320x240")
    dialog = ExportDialog(make_project(source, duration=1.0))
    shown: list[str] = []
    monkeypatch.setattr(dialog, "_show_failure_details", shown.append)
    # A directory that doesn't exist: ffmpeg fails to open the output.
    dialog._path_edit.setText(str(tmp_path / "missing_dir" / "out.mp4"))

    dialog._start_export()

    assert pump_until(qapp, lambda: not dialog._exporting)
    assert dialog._status_label.text() == "Export failed."
    assert not dialog._status_label.isHidden()
    assert len(shown) == 1 and "ffmpeg exited" in shown[0]


def test_real_export_draws_title_cards(qapp, make_video_clip, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache"))
    source = make_video_clip("ed_title.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)
    project.tracks[0].items.insert(0, new_title_clip(Title(text="")))
    dialog = ExportDialog(project)
    output_path = tmp_path / "titled.mp4"
    dialog._path_edit.setText(str(output_path))

    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog._start_export()
    assert pump_until(qapp, lambda: accepted)

    info = probe(output_path)
    assert abs(info.duration - Fraction(7)) < Fraction(1, 5)
    frame = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", "2.5", "-i", str(output_path),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    red, green, blue = frame[0], frame[1], frame[2]
    assert blue > 60 and red < 20 and green < 50  # the dark blue card (#012456)
