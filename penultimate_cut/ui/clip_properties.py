"""Clip properties panel: shows and edits the selected clip's details.

Fit Mode (black bars / blurred background / crop to fill), which CLAUDE.md's
Export section calls out as needing to be prominent here; the clip's sound
(volume, mute); fade in from / out to black; and basic read-only info
(filename, duration). Speed and rotate are Phase 2.

The panel never edits a clip itself: it emits what the user asked for, and
the main window turns that into an undoable command.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QWidget,
)

from penultimate_cut.model.project import MAX_VOLUME, Clip, FitMode
from penultimate_cut.ui.format import format_duration
from penultimate_cut.ui.sliders import click_to_jump

_FIT_MODE_LABELS: dict[FitMode, str] = {
    "black_bars": "Black bars",
    "blurred_background": "Blurred background",
    "crop_to_fill": "Crop to fill",
}


class ClipPropertiesPanel(QWidget):
    fit_mode_changed = Signal(str)  # FitMode
    options_changed = Signal(str, object)
    """(undo text, options for `SetClipOptionsCommand`), e.g.
    ("Mute", {"muted": True})."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clip: Clip | None = None
        self._updating = False  # guards the signals while we're just displaying, not editing

        self._name_label = QLabel("No clip selected")
        self._duration_label = QLabel("")
        self._fit_mode_combo = QComboBox()
        for mode, label in _FIT_MODE_LABELS.items():
            self._fit_mode_combo.addItem(label, mode)
        self._fit_mode_combo.currentIndexChanged.connect(self._on_fit_mode_changed)

        # One undo step per drag: the value is only reported on release.
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, MAX_VOLUME)
        self._volume_slider.setPageStep(10)
        self._volume_slider.setTracking(False)
        self._volume_slider.setToolTip("How loud this clip is in the finished video")
        click_to_jump(self._volume_slider)
        self._volume_label = QLabel()
        self._volume_label.setMinimumWidth(self.fontMetrics().horizontalAdvance("200%") + 4)
        self._volume_slider.sliderMoved.connect(self._show_volume)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._mute_box = QCheckBox("Mute")
        self._mute_box.toggled.connect(self._on_mute_toggled)
        self._fade_in_box = QCheckBox("Fade in from black")
        self._fade_in_box.toggled.connect(self._on_fade_in_toggled)
        self._fade_out_box = QCheckBox("Fade out to black")
        self._fade_out_box.toggled.connect(self._on_fade_out_toggled)

        volume_row = QHBoxLayout()
        volume_row.addWidget(self._volume_slider, 1)
        volume_row.addWidget(self._volume_label)

        layout = QFormLayout(self)
        layout.addRow(self._name_label)
        layout.addRow("Duration", self._duration_label)
        layout.addRow("Fit mode", self._fit_mode_combo)
        layout.addRow("Volume", volume_row)
        layout.addRow("", self._mute_box)
        layout.addRow("Fades", self._fade_in_box)
        layout.addRow("", self._fade_out_box)

        # A long filename must not widen the panel (and squeeze the preview):
        # the label ignores its text's width, the text is elided to fit (full
        # name in the tooltip), and the panel is capped a little wider than
        # its widest row needs.
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
            has_video = clip is not None and clip.media_ref.has_video
            has_audio = clip is not None and clip.media_ref.has_audio
            # Fit Mode only means something for a clip with a video frame to
            # letterbox -- an audio-only clip has nothing to fit.
            self._fit_mode_combo.setEnabled(has_video)
            self._volume_slider.setEnabled(has_audio and clip is not None and not clip.muted)
            self._mute_box.setEnabled(has_audio)
            self._fade_in_box.setEnabled(clip is not None)
            self._fade_out_box.setEnabled(clip is not None)
            if clip is None:
                self._full_name = "No clip selected"
                self._show_name()
                self._duration_label.setText("")
                self._volume_slider.setValue(100)
                self._volume_label.setText("")
                for box in (self._mute_box, self._fade_in_box, self._fade_out_box):
                    box.setChecked(False)
                return
            self._full_name = clip.media_ref.path.name
            self._show_name()
            self._duration_label.setText(format_duration(clip.duration))
            self._fit_mode_combo.setCurrentIndex(self._fit_mode_combo.findData(clip.fit_mode))
            self._volume_slider.setValue(clip.volume)
            self._show_volume(clip.volume if has_audio else None)
            self._mute_box.setChecked(clip.muted)
            self._fade_in_box.setChecked(clip.fade_in)
            self._fade_out_box.setChecked(clip.fade_out)
        finally:
            self._updating = False

    def _show_volume(self, volume: int | None) -> None:
        self._volume_label.setText("" if volume is None else f"{volume}%")

    def _on_fit_mode_changed(self, index: int) -> None:
        if self._updating or self._clip is None:
            return
        new_mode = self._fit_mode_combo.itemData(index)
        if new_mode != self._clip.fit_mode:
            self.fit_mode_changed.emit(new_mode)

    def _on_volume_changed(self, volume: int) -> None:
        self._show_volume(volume)
        if not self._updating and self._clip is not None and volume != self._clip.volume:
            self.options_changed.emit("Change volume", {"volume": volume})

    def _on_mute_toggled(self, muted: bool) -> None:
        if not self._updating and self._clip is not None:
            self.options_changed.emit("Mute" if muted else "Unmute", {"muted": muted})

    def _on_fade_in_toggled(self, on: bool) -> None:
        if not self._updating and self._clip is not None:
            self.options_changed.emit("Fade in" if on else "Remove fade in", {"fade_in": on})

    def _on_fade_out_toggled(self, on: bool) -> None:
        if not self._updating and self._clip is not None:
            self.options_changed.emit("Fade out" if on else "Remove fade out", {"fade_out": on})
