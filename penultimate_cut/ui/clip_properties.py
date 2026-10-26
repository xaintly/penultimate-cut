"""Clip properties panel: shows and edits the selected clip's details.

Phase 1 scope: Fit Mode (black bars / blurred background / crop to fill),
which CLAUDE.md's Export section calls out as needing to be prominent here,
plus basic read-only info (filename, duration). The architecture comment
also lists volume, speed, fade, and rotate -- all Phase 2, and none of them
have an editable model field or command yet, so there's nothing to show for
them here until that lands.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QSizePolicy, QWidget

from penultimate_cut.model.project import Clip, FitMode
from penultimate_cut.ui.format import format_duration

_FIT_MODE_LABELS: dict[FitMode, str] = {
    "black_bars": "Black bars",
    "blurred_background": "Blurred background",
    "crop_to_fill": "Crop to fill",
}


class ClipPropertiesPanel(QWidget):
    fit_mode_changed = Signal(str)  # FitMode

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clip: Clip | None = None
        self._updating = False  # guards fit_mode_changed while we're just displaying, not editing

        self._name_label = QLabel("No clip selected")
        self._duration_label = QLabel("")
        self._fit_mode_combo = QComboBox()
        for mode, label in _FIT_MODE_LABELS.items():
            self._fit_mode_combo.addItem(label, mode)
        self._fit_mode_combo.currentIndexChanged.connect(self._on_fit_mode_changed)

        layout = QFormLayout(self)
        layout.addRow(self._name_label)
        layout.addRow("Duration", self._duration_label)
        layout.addRow("Fit mode", self._fit_mode_combo)

        # A long filename must not widen the panel (and squeeze the preview):
        # the label ignores its text's width, the text is elided to fit (full
        # name in the tooltip), and the panel is capped a little wider than
        # its "Fit mode" row needs.
        self._full_name = ""
        self._name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMaximumWidth(self.sizeHint().width() + 2 * self.fontMetrics().averageCharWidth())

        self.set_clip(None)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._show_name()

    def _show_name(self) -> None:
        elided = self._name_label.fontMetrics().elidedText(
            self._full_name, Qt.TextElideMode.ElideMiddle, self._name_label.width()
        )
        self._name_label.setText(elided)
        self._name_label.setToolTip(self._full_name if elided != self._full_name else "")

    def set_clip(self, clip: Clip | None) -> None:
        """Display `clip`'s properties, or clear the panel if `None`
        (nothing selected, or the selection is a gap rather than a clip)."""
        self._clip = clip
        self._updating = True
        try:
            if clip is None:
                self._full_name = "No clip selected"
                self._show_name()
                self._duration_label.setText("")
                self._fit_mode_combo.setEnabled(False)
                return
            self._full_name = clip.media_ref.path.name
            self._show_name()
            self._duration_label.setText(format_duration(clip.duration))
            # Fit Mode only means something for a clip with a video frame to
            # letterbox -- an audio-only clip has nothing to fit.
            self._fit_mode_combo.setEnabled(clip.media_ref.has_video)
            self._fit_mode_combo.setCurrentIndex(self._fit_mode_combo.findData(clip.fit_mode))
        finally:
            self._updating = False

    def _on_fit_mode_changed(self, index: int) -> None:
        if self._updating or self._clip is None:
            return
        new_mode = self._fit_mode_combo.itemData(index)
        if new_mode != self._clip.fit_mode:
            self.fit_mode_changed.emit(new_mode)
