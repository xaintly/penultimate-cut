"""Clip properties panel: shows and edits the selected clip's details.

Fit Mode (black bars / blurred background / crop to fill), which CLAUDE.md's
Export section calls out as needing to be prominent here; the clip's sound
(volume, mute); fade in from / out to black; and basic read-only info
(filename, duration). Speed and rotate are Phase 2.

A title card shows its text and colors instead of Fit Mode and sound (it
always fills the picture and has none). The text is sent as it's typed, a
short pause at a time, so the preview keeps up; those edits merge into one
undo step (see `SetTitleCommand`). Each carries the clip's id, not just
"the selected clip": a pause still pending when another clip is selected
must land on the title it was typed into.

The panel never edits a clip itself: it emits what the user asked for, and
the main window turns that into an undoable command.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QWidget,
)

from penultimate_cut.model.project import MAX_VOLUME, Clip, FitMode, Title
from penultimate_cut.ui.format import format_duration
from penultimate_cut.ui.sliders import click_to_jump

_FIT_MODE_LABELS: dict[FitMode, str] = {
    "black_bars": "Black bars",
    "blurred_background": "Blurred background",
    "crop_to_fill": "Crop to fill",
}

TITLE_TYPING_PAUSE_MS = 400
"""How long typing has to pause before the title text is applied."""


class ClipPropertiesPanel(QWidget):
    fit_mode_changed = Signal(str)  # FitMode
    options_changed = Signal(str, object)
    """(undo text, options for `SetClipOptionsCommand`), e.g.
    ("Mute", {"muted": True})."""
    title_changed = Signal(str, str, object, bool)
    """(undo text, clip id, new `Title`, typing) for `SetTitleCommand`."""

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

        self._title_text = QPlainTextEdit()
        self._title_text.setTabChangesFocus(True)
        self._title_text.setToolTip("The title's words. Press Enter for a new line.")
        lines = 3
        self._title_text.setFixedHeight(
            self._title_text.fontMetrics().lineSpacing() * lines
            + 2 * round(self._title_text.document().documentMargin())
            + 2 * self._title_text.frameWidth()
        )
        # Its own size hint is wide; the panel's width shouldn't follow it.
        self._title_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._title_text.textChanged.connect(self._on_title_text_changed)
        self._title_text.installEventFilter(self)
        self._pending_text: tuple[str, Title] | None = None
        """(clip id, title) typed but not yet sent."""
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(TITLE_TYPING_PAUSE_MS)
        self._text_timer.timeout.connect(self._send_pending_text)
        self._background_button = QPushButton("Choose…")
        self._background_button.clicked.connect(self._choose_background)
        self._text_color_button = QPushButton("Choose…")
        self._text_color_button.clicked.connect(self._choose_text_color)

        volume_row = QHBoxLayout()
        volume_row.addWidget(self._volume_slider, 1)
        volume_row.addWidget(self._volume_label)

        layout = QFormLayout(self)
        self._layout = layout
        layout.addRow(self._name_label)
        layout.addRow("Duration", self._duration_label)
        layout.addRow("Text", self._title_text)
        layout.addRow("Background", self._background_button)
        layout.addRow("Text color", self._text_color_button)
        layout.addRow("Fit mode", self._fit_mode_combo)
        layout.addRow("Volume", volume_row)
        layout.addRow("", self._mute_box)
        layout.addRow("Fades", self._fade_in_box)
        layout.addRow("", self._fade_out_box)
        self._volume_row = volume_row

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Leaving the text box sends what's been typed right away.
        if watched is self._title_text and event.type() == QEvent.Type.FocusOut:
            self._send_pending_text()
        return super().eventFilter(watched, event)

    def _show_name(self) -> None:
        elided = self._name_label.fontMetrics().elidedText(
            self._full_name, Qt.TextElideMode.ElideMiddle, self._name_label.width()
        )
        self._name_label.setText(elided)
        self._name_label.setToolTip(self._full_name if elided != self._full_name else "")

    def set_clip(self, clip: Clip | None) -> None:
        """Display `clip`'s properties, or clear the panel if `None`
        (nothing selected, or the selection is a gap rather than a clip)."""
        if self._pending_text is not None and (clip is None or clip.id != self._pending_text[0]):
            self._send_pending_text()  # typed into the clip being left
        self._clip = clip
        self._updating = True
        try:
            title = clip.media_ref.title if clip is not None else None
            has_video = clip is not None and clip.media_ref.has_video
            has_audio = clip is not None and clip.media_ref.has_audio
            for field in (self._title_text, self._background_button, self._text_color_button):
                self._layout.setRowVisible(field, title is not None)
            for row in (self._fit_mode_combo, self._volume_row, self._mute_box):
                self._layout.setRowVisible(row, title is None)
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
            self._full_name = "Title" if title is not None else clip.media_ref.display_name
            self._show_name()
            self._duration_label.setText(format_duration(clip.duration))
            self._fit_mode_combo.setCurrentIndex(self._fit_mode_combo.findData(clip.fit_mode))
            self._volume_slider.setValue(clip.volume)
            self._show_volume(clip.volume if has_audio else None)
            self._mute_box.setChecked(clip.muted)
            self._fade_in_box.setChecked(clip.fade_in)
            self._fade_out_box.setChecked(clip.fade_out)
            if title is not None:
                self._show_title(title)
        finally:
            self._updating = False

    def edit_title_text(self) -> None:
        """Put the cursor in the title's text, all of it selected, so typing
        replaces the placeholder."""
        self._title_text.setFocus()
        self._title_text.selectAll()

    def _show_title(self, title: Title) -> None:
        # Only when it differs: after each pause the text comes back from
        # the edit just made, and resetting it would move the cursor.
        if self._title_text.toPlainText() != title.text:
            self._title_text.setPlainText(title.text)
        self._background_button.setIcon(_swatch(title.background))
        self._text_color_button.setIcon(_swatch(title.text_color))

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

    # --- title ---------------------------------------------------------------------

    def _on_title_text_changed(self) -> None:
        if self._updating or self._clip is None or self._clip.media_ref.title is None:
            return
        title = replace(self._clip.media_ref.title, text=self._title_text.toPlainText())
        self._pending_text = (self._clip.id, title)
        self._text_timer.start()

    def _send_pending_text(self) -> None:
        self._text_timer.stop()
        pending, self._pending_text = self._pending_text, None
        if pending is not None:
            clip_id, title = pending
            self.title_changed.emit("Edit title", clip_id, title, True)

    def _choose_background(self) -> None:
        self._choose_color("background", "Title background", "Change title background")

    def _choose_text_color(self) -> None:
        self._choose_color("text_color", "Title text color", "Change title text color")

    def _choose_color(self, field: str, dialog_title: str, undo_text: str) -> None:
        self._send_pending_text()
        if self._clip is None or self._clip.media_ref.title is None:
            return
        clip_id, title = self._clip.id, self._clip.media_ref.title
        # Parented to the window, like the app's menus (see ui/preview_widget.py).
        color = QColorDialog.getColor(QColor(getattr(title, field)), self.window(), dialog_title)
        if color.isValid() and color.name() != getattr(title, field):
            new_title = replace(title, **{field: color.name()})
            self.title_changed.emit(undo_text, clip_id, new_title, False)


def _swatch(color: str) -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)
