"""Main window: preview (top), media bin (left), timeline (bottom).

Owns the one `Project` and `QUndoStack` for the open document (Phase 1 has
no File > New/Open yet, so this starts as a fresh empty project) and wires
the three real widgets together:

- Media bin double-click -> a default `Clip` for that media, appended to
  the timeline via an `InsertCommand`.
- Timeline click/drag -> seeks the preview; the preview's own position
  (moving during playback) -> moves the timeline's playhead. Only the
  user-driven direction (`TimelineWidget.playhead_moved`) feeds the other
  widget; `PreviewWidget.position_changed` only updates the timeline's
  *visual* playhead (`set_playhead`, which doesn't itself emit
  `playhead_moved`), so this can't become a feedback loop.
- `QUndoStack.indexChanged` (fires on every push/undo/redo) -> reloads the
  preview's EDL, since any edit changes what it should be playing. Skipped
  while the project is empty -- an EDL with no segments at all isn't a
  state preview/edl.py or the player have been exercised against.
"""

from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QUndoStack
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from penultimate_cut.model.project import Project, Track, default_clip_for
from penultimate_cut.ui.media_bin import MediaBin, MediaBinEntry
from penultimate_cut.ui.preview_widget import PreviewWidget
from penultimate_cut.ui.timeline.widget import TimelineWidget


def _placeholder(text: str) -> QFrame:
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(frame)
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    return frame


class MainWindow(QMainWindow):
    def __init__(self, **preview_player_options: object) -> None:
        """`preview_player_options` are forwarded to the preview's `Player`
        -- see its own docstring; a test-only escape hatch, nothing in
        normal use passes any."""
        super().__init__()
        self.setWindowTitle("Penultimate Cut")
        self.resize(1280, 800)

        self.project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
        self.undo_stack = QUndoStack(self)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        self.media_bin = MediaBin()
        self.preview = PreviewWidget(**preview_player_options)  # type: ignore[arg-type]
        self.timeline = TimelineWidget()
        self.timeline.set_project(self.project, self.undo_stack)

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.addWidget(self.media_bin)
        top_split.addWidget(self.preview)
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 3)

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.timeline)
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)

        root_layout.addWidget(main_split)

        self._import_button = QPushButton("Import")
        self._split_button = QPushButton("Split")
        self._delete_button = QPushButton("Delete")
        self._import_button.clicked.connect(self.media_bin.choose_files)
        self._split_button.clicked.connect(self.timeline.split_at_playhead)
        self._delete_button.clicked.connect(lambda: self.timeline.delete_selected(ripple=True))

        button_row = QHBoxLayout()
        button_row.addWidget(self._import_button)
        button_row.addWidget(self._split_button)
        button_row.addWidget(self._delete_button)
        for label in ("Add Title", "Export"):
            button_row.addWidget(QPushButton(label))
        root_layout.addLayout(button_row)

        self.media_bin.media_double_clicked.connect(self._on_media_double_clicked)
        self.timeline.playhead_moved.connect(self.preview.seek)
        self.preview.position_changed.connect(self._on_preview_position_changed)
        self.undo_stack.indexChanged.connect(self._on_undo_stack_changed)

        self._init_shortcuts()

    def _init_shortcuts(self) -> None:
        shortcuts = [
            ("Space", self.preview.toggle_play_pause),
            ("S", self.timeline.split_at_playhead),
            ("Delete", lambda: self.timeline.delete_selected(ripple=True)),
            ("Shift+Delete", lambda: self.timeline.delete_selected(ripple=False)),
            ("Ctrl+Z", self.undo_stack.undo),
            ("Ctrl+Y", self.undo_stack.redo),
            ("+", self.timeline.zoom_in),
            ("-", self.timeline.zoom_out),
        ]
        self._shortcuts = [QShortcut(QKeySequence(key), self) for key, _ in shortcuts]
        for shortcut, (_key, handler) in zip(self._shortcuts, shortcuts, strict=True):
            shortcut.activated.connect(handler)

    def _on_media_double_clicked(self, entry: MediaBinEntry) -> None:
        self.timeline.insert_clip(default_clip_for(entry.media_ref))

    def _on_preview_position_changed(self, position: Fraction | None) -> None:
        if position is not None:
            self.timeline.set_playhead(position)

    def _on_undo_stack_changed(self, _index: int) -> None:
        if self.project.duration > 0:
            self.preview.load_project(self.project)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.media_bin.shutdown()
        self.timeline.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
