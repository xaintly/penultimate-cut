"""Export dialog: resolution, quality, output file -- the three choices
CLAUDE.md's Export section calls for, plus a live estimated file size and,
once started, a progress bar with time remaining and a Cancel button.

Export runs on a background thread (export/runner.py's `run_export` is a
blocking call) so the UI never blocks; progress comes back through a Qt
signal, the same cross-thread relay pattern used by every other worker in
this app. Cancellation is a plain boolean flag set by the Cancel button and
read by the worker thread's `cancel_check` -- safe without a lock, since
CPython's GIL already makes a single attribute's read/write atomic, and
`run_export` only ever reads it, never reads-then-writes.

The encoder itself (libx264) isn't one of the three choices -- CLAUDE.md's
licensing section keeps that behind one function in encoders.py precisely
so it's not user-facing yet.
"""

from __future__ import annotations

import threading
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from penultimate_cut.export.encoders import (
    EncoderName,
    VideoQuality,
    encoder_args,
    estimate_file_size,
)
from penultimate_cut.export.filtergraph import (
    FiltergraphError,
    ResolutionChoice,
    build_filtergraph,
    target_resolution,
)
from penultimate_cut.export.runner import ExportCancelled, ExportProgress, RunnerError, run_export
from penultimate_cut.model.project import Project
from penultimate_cut.ui.format import format_duration

_RESOLUTION_LABELS: dict[ResolutionChoice, str] = {
    "auto": "Automatic",  # shown with its actual size, e.g. "Automatic (1920×1080)"
    "1080p": "1080p",
    "720p": "720p",
    "480p": "480p",
    "360p": "360p",
}
_QUALITY_LABELS: dict[VideoQuality, str] = {
    "small_file": "Small file",
    "balanced": "Balanced",
    "high_quality": "High quality",
}
_DEFAULT_QUALITY_INDEX = 1  # "Balanced"
_ENCODER: EncoderName = "libx264"
_PROGRESS_STEPS = 1000


class _ExportSignals(QObject):
    progress = Signal(object)  # ExportProgress
    finished = Signal()
    cancelled = Signal()
    failed = Signal(str)


class ExportDialog(QDialog):
    def __init__(
        self,
        project: Project,
        default_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export")
        self._project = project
        self._exporting = False
        self._cancel_requested = False

        self._signals = _ExportSignals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.cancelled.connect(self._on_cancelled)
        self._signals.failed.connect(self._on_failed)

        self._resolution_combo = QComboBox()
        for resolution_value, label in _RESOLUTION_LABELS.items():
            if resolution_value == "auto":
                auto_width, auto_height = target_resolution(project, "auto")
                label = f"{label} ({auto_width}×{auto_height})"
            self._resolution_combo.addItem(label, resolution_value)

        self._quality_combo = QComboBox()
        for quality_value, label in _QUALITY_LABELS.items():
            self._quality_combo.addItem(label, quality_value)
        self._quality_combo.setCurrentIndex(_DEFAULT_QUALITY_INDEX)

        self._path_edit = QLineEdit(str(default_path or Path.home() / "export.mp4"))
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._choose_path)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_button)

        self._size_label = QLabel()

        form = QFormLayout()
        form.addRow("Resolution", self._resolution_combo)
        form.addRow("Quality", self._quality_combo)
        form.addRow("File", path_row)
        form.addRow("Estimated size", self._size_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, _PROGRESS_STEPS)
        self._status_label = QLabel()
        self._progress_bar.hide()
        self._status_label.hide()

        self._buttons = QDialogButtonBox()
        self._export_button = self._buttons.addButton(
            "Export", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._cancel_button = self._buttons.addButton(
            "Cancel", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._export_button.clicked.connect(self._start_export)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._status_label)
        layout.addWidget(self._buttons)

        self._resolution_combo.currentIndexChanged.connect(self._update_estimate)
        self._quality_combo.currentIndexChanged.connect(self._update_estimate)
        self._update_estimate()

    # --- live size estimate -------------------------------------------------

    def _update_estimate(self) -> None:
        width, height = target_resolution(self._project, self._resolution_combo.currentData())
        quality = self._quality_combo.currentData()
        size = estimate_file_size(
            quality, width, height, self._project.frame_rate, self._project.duration
        )
        self._size_label.setText(f"~{_format_size(size)}")

    # --- output file picker --------------------------------------------------

    def _choose_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export video", self._path_edit.text(), "MP4 video (*.mp4)"
        )
        if path:
            self._path_edit.setText(path)

    # --- export lifecycle -----------------------------------------------------

    def _start_export(self) -> None:
        output_path = Path(self._path_edit.text())
        if output_path.suffix.lower() != ".mp4":
            # Output is always MP4 (see CLAUDE.md's Export section); the save
            # dialog's filter usually adds this itself, but that's platform-
            # dependent, and the user can type a path directly without going
            # through the dialog at all.
            output_path = output_path.with_suffix(".mp4")
            self._path_edit.setText(str(output_path))
        resolution = self._resolution_combo.currentData()
        quality = self._quality_combo.currentData()

        try:
            plan = build_filtergraph(self._project, resolution)
        except FiltergraphError as exc:
            self._status_label.show()
            self._status_label.setText(f"Can't export: {exc}")
            return

        args: list[str] = []
        for input_arg in plan.input_args:
            args.extend(input_arg)
        args += [
            "-filter_complex",
            plan.filter_complex,
            "-map",
            plan.video_label,
            "-map",
            plan.audio_label,
        ]
        args += encoder_args(_ENCODER, quality, plan.width, plan.height, plan.frame_rate)

        self._cancel_requested = False
        self._set_exporting(True)
        thread = threading.Thread(
            target=self._run_export_worker,
            args=(args, output_path, self._project.duration),
            daemon=True,
        )
        thread.start()

    def _run_export_worker(self, args: list[str], output_path: Path, duration: Fraction) -> None:
        """Runs on a worker thread -- no Qt widget access here."""
        try:
            run_export(
                args,
                output_path,
                duration,
                on_progress=lambda p: self._signals.progress.emit(p),
                cancel_check=lambda: self._cancel_requested,
            )
            self._signals.finished.emit()
        except ExportCancelled:
            self._signals.cancelled.emit()
        except RunnerError as exc:
            self._signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 -- an uncaught error here would leave the dialog stuck "exporting"
            self._signals.failed.emit(f"Unexpected error: {exc}")

    def _on_cancel_clicked(self) -> None:
        if self._exporting:
            self._cancel_requested = True
        else:
            self.reject()

    # --- Qt slots, main thread only --------------------------------------------

    def _on_progress(self, progress: ExportProgress) -> None:
        self._progress_bar.setValue(int(progress.fraction * _PROGRESS_STEPS))
        if progress.speed > 0:
            remaining_seconds = (
                (1 - progress.fraction) * float(self._project.duration) / progress.speed
            )
            remaining = format_duration(Fraction(remaining_seconds).limit_denominator(1000))
            self._status_label.setText(f"About {remaining} remaining")
        else:
            self._status_label.setText("Estimating time remaining…")

    def _on_finished(self) -> None:
        self._set_exporting(False)
        self._status_label.setText("Done.")
        self.accept()

    def _on_cancelled(self) -> None:
        self._set_exporting(False)
        self._status_label.setText("Export cancelled.")
        self._status_label.show()

    def _on_failed(self, message: str) -> None:
        self._set_exporting(False)
        self._status_label.setText("Export failed.")
        self._status_label.show()
        self._show_failure_details(message)

    def _show_failure_details(self, message: str) -> None:
        """A short plain-language message, with ffmpeg's own output behind
        "Show Details..." (selectable, so it can be copied into a bug report)
        rather than dumped into the dialog."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Export failed")
        box.setText("The video couldn't be exported.")
        box.setInformativeText("No file was saved. Click Show Details for the technical reason.")
        box.setDetailedText(message)
        box.exec()

    def _set_exporting(self, exporting: bool) -> None:
        self._exporting = exporting
        self._resolution_combo.setEnabled(not exporting)
        self._quality_combo.setEnabled(not exporting)
        self._path_edit.setEnabled(not exporting)
        self._export_button.setEnabled(not exporting)
        self._progress_bar.setVisible(exporting)
        self._status_label.setVisible(exporting)
        if exporting:
            self._progress_bar.setValue(0)


def _format_size(num_bytes: int) -> str:
    megabytes = num_bytes / 1_000_000
    if megabytes >= 1000:
        return f"{megabytes / 1000:.1f} GB"
    return f"{megabytes:.0f} MB"
