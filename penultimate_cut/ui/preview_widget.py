"""Preview widget: embeds the libmpv player and its transport controls.

Owns a `preview.player.Player`, embedding mpv's video output into a plain
child widget via its native window id. Exposes play/pause/seek/frame-step
as plain methods so `ui/main_window.py`'s global keyboard shortcuts can
drive it without knowing anything about mpv.

mpv's position-changed callback fires on mpv's own event thread, not Qt's --
it's relayed through a Qt signal (safe to emit cross-thread; Qt queues the
connected slot onto the receiving object's thread automatically) rather than
touching widgets directly from that callback.
"""

from __future__ import annotations

import contextlib
from fractions import Fraction

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from penultimate_cut.model.project import Project
from penultimate_cut.preview.edl import to_edl
from penultimate_cut.preview.player import FrameStepDirection, Player
from penultimate_cut.ui.format import format_duration

_SLIDER_RESOLUTION = 1000
"""Seek slider steps per second of timeline -- fine enough for smooth scrubbing
without needing frame-accurate slider positions (the frame grid is much finer)."""


class PreviewWidget(QWidget):
    position_changed = Signal(
        object
    )  # Fraction | None; object avoids a C++ signal type for Fraction
    volume_changed = Signal(int)  # 0-100, from the user moving the volume slider
    _playing_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None, **player_options: object) -> None:
        """`player_options` are forwarded to `Player(...)` -- see its own
        docstring; a test-only escape hatch, production code passes none."""
        super().__init__(parent)

        self._video_surface = QWidget(self)
        # Making this native also makes its ancestors (and, in turn, their
        # siblings -- nearly the whole main window) native. A popup menu
        # parented to one of those makes Qt warn "QWidgetWindow(...) must be
        # a top level window", so parent menus to `self.window()` instead.
        # Don't "fix" the cascade with WA_DontCreateNativeAncestors: tried,
        # and embedded mpv then intermittently hangs the app at load.
        self._video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._video_surface.setStyleSheet("background-color: black;")

        self._play_button = QPushButton("Play")
        self._back_button = QPushButton("◁|")
        self._forward_button = QPushButton("|▷")
        self._time_label = QLabel("00:00 / 00:00")
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        # Preview volume only -- how loud *this playback* is while editing.
        # Not part of the project, never affects export.
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setFixedWidth(90)
        self._volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep ←/→ for frame stepping
        self._volume_slider.setToolTip("Preview volume (doesn't change the exported video)")

        transport = QHBoxLayout()
        transport.addWidget(self._back_button)
        transport.addWidget(self._play_button)
        transport.addWidget(self._forward_button)
        transport.addWidget(self._slider)
        transport.addWidget(self._time_label)
        transport.addSpacing(12)
        transport.addWidget(QLabel("Volume"))
        transport.addWidget(self._volume_slider)

        layout = QVBoxLayout(self)
        layout.addWidget(self._video_surface, 1)
        layout.addLayout(transport)

        self._dragging = False
        self._duration: Fraction | None = None

        self._player = Player(
            wid=int(self._video_surface.winId()),
            **player_options,  # type: ignore[arg-type]
        )
        self._player.on_position_changed(lambda pos: self.position_changed.emit(pos))
        self.position_changed.connect(self._on_position_changed)
        # Same cross-thread relay, so the button label follows mpv's actual
        # state -- e.g. mpv pausing itself at the end of the timeline.
        self._player.on_playing_changed(lambda playing: self._playing_changed.emit(playing))
        self._playing_changed.connect(self._on_playing_changed)
        # mpv starts unpaused; with nothing loaded the button would say "Pause".
        self._player.pause()

        self._play_button.clicked.connect(self.toggle_play_pause)
        self._back_button.clicked.connect(lambda: self.frame_step("backward"))
        self._forward_button.clicked.connect(lambda: self.frame_step("forward"))
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._volume_slider.setValue(self._player.volume)
        self._volume_slider.valueChanged.connect(self._on_volume_slider_changed)

    # --- lifecycle -------------------------------------------------------------

    def shutdown(self) -> None:
        """Release the underlying mpv instance. Call explicitly before this
        widget is destroyed -- Qt/Python teardown order isn't reliable enough
        to depend on for releasing an external process's resources."""
        self._player.close()

    @property
    def player(self) -> Player:
        return self._player

    # --- project loading ---------------------------------------------------------

    def load_project(self, project: Project, start: Fraction = Fraction(0)) -> None:
        """Load `project` (paused), positioned at `start`."""
        self._player.load_edl(to_edl(project), start=min(start, project.duration))
        # mpv starts playing a freshly loaded file immediately (its `pause`
        # property defaults to False and loading a new file doesn't change
        # it) -- confirmed this reliably sticks even without waiting for the
        # load to finish. Without this, every edit (every `load_project`
        # call, since `MainWindow` reloads on every undo-stack change) would
        # silently resume playback the user never asked for.
        self._player.pause()
        self._play_button.setText("Play")

    def clear(self) -> None:
        """Show nothing: the timeline is empty."""
        self._player.unload()
        self._duration = None
        self._slider.setRange(0, 0)
        self._time_label.setText(f"{format_duration(None)} / {format_duration(None)}")

    # --- transport ----------------------------------------------------------------

    def play(self) -> None:
        self._player.play()
        self._play_button.setText("Pause")

    def pause(self) -> None:
        self._player.pause()
        self._play_button.setText("Play")

    def toggle_play_pause(self) -> None:
        self._player.toggle_play_pause()
        self._play_button.setText("Pause" if self._player.is_playing else "Play")

    def seek(self, position: Fraction) -> None:
        self._player.seek(position)

    def frame_step(self, direction: FrameStepDirection = "forward") -> None:
        self._player.frame_step(direction)

    # --- preview volume -------------------------------------------------------------

    @property
    def volume(self) -> int:
        return self._volume_slider.value()

    def set_volume(self, volume: int) -> None:
        self._volume_slider.setValue(volume)  # -> _on_volume_slider_changed

    def _on_volume_slider_changed(self, volume: int) -> None:
        self._player.volume = volume
        self._volume_slider.setToolTip(
            f"Preview volume: {volume}% (doesn't change the exported video)"
        )
        self.volume_changed.emit(volume)

    # --- internal: slider <-> player position sync -------------------------------

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        self._player.seek(_slider_value_to_seconds(self._slider.value()))

    def _on_slider_moved(self, value: int) -> None:
        self._player.seek(_slider_value_to_seconds(value))

    def _on_playing_changed(self, playing: bool) -> None:
        self._play_button.setText("Pause" if playing else "Play")

    def _on_position_changed(self, position: Fraction | None) -> None:
        # This callback is relayed from mpv's own event thread via a queued
        # Qt signal; it can arrive after shutdown() has already closed the
        # player (a real, reachable ordering -- not hypothetical), in which
        # case reading any property raises python-mpv's ShutdownError (a
        # SystemError subclass). Nothing useful to do with a position update
        # for a player that's already gone, so just drop it.
        with contextlib.suppress(SystemError):
            self._update_for_position(position)

    def _update_for_position(self, position: Fraction | None) -> None:
        duration = self._player.duration
        if duration is not None and duration != self._duration:
            self._duration = duration
            self._slider.setRange(0, int(duration * _SLIDER_RESOLUTION))

        if position is not None and not self._dragging:
            self._slider.setValue(int(position * _SLIDER_RESOLUTION))

        self._time_label.setText(f"{format_duration(position)} / {format_duration(self._duration)}")


def _slider_value_to_seconds(value: int) -> Fraction:
    return Fraction(value, _SLIDER_RESOLUTION)
