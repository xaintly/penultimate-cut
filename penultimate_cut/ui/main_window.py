"""Main window: preview (top), media bin (left), timeline (bottom).

Owns the one `Project` and `QUndoStack` for the open document and wires the
real widgets together:

- Media bin double-click -> a default `Clip` for that media, appended to
  the timeline via an `InsertCommand`.
- Timeline click/drag -> seeks the preview; the preview's own position
  (moving during playback) -> moves the timeline's playhead. Only the
  user-driven direction (`TimelineWidget.playhead_moved`) feeds the other
  widget; `PreviewWidget.position_changed` only updates the timeline's
  *visual* playhead (`set_playhead`, which doesn't itself emit
  `playhead_moved`), so this can't become a feedback loop.
- `QUndoStack.indexChanged` (fires on every push/undo/redo) -> reloads the
  preview's EDL, since any edit changes what it should be playing (skipped
  while the project is empty); re-displays the selected clip's properties,
  since e.g. undoing a trim changes the very clip object `_selected_index`
  points at; and refreshes the window title's unsaved-changes marker.
- Timeline selection -> the clip properties panel displays that clip (or
  clears itself for a gap or no selection); the panel's own Fit Mode
  control -> a `SetFitModeCommand` for whichever clip is selected.
- Export button -> a modal `ExportDialog` for the current project.
- File menu: New/Open/Save/Save As, each going through
  `_confirm_discard_if_dirty()` first if there are unsaved changes (tracked
  via `QUndoStack`'s own clean/dirty state, set clean on every successful
  save, new, or open). New/Open both replace `self.project` and
  `self.undo_stack` wholesale via `_load_fresh_project()` -- which is also
  why the Ctrl+Z/Ctrl+Y shortcuts below are wrapped in lambdas rather than
  bound to `self.undo_stack.undo`/`.redo` directly: a bound method captured
  once would keep calling the *original* stack's methods forever, even
  after `self.undo_stack` has been reassigned to a different object.
  Opening a project with missing media offers to search a folder for them
  (`_offer_relink`, matching by filename via `otio_io.find_relink_candidates`)
  before it's displayed, and repopulates the media bin from everything the
  loaded project actually references.
- Autosave: a `QTimer` writes a snapshot every 60 seconds (skipped while the
  undo stack is clean -- nothing's changed since the last save, if any) via
  `io/autosave.py`, plus once more right before an export starts. Launching
  with a recoverable autosave for a never-saved project, or opening a project
  whose own autosave is newer than it, offers to load that instead
  (`_maybe_recover_autosave`) -- recovered content is loaded under the
  *original* path (or none) but deliberately left dirty, since it was never
  actually written to that path. A successful explicit save, or the user
  choosing Discard on the unsaved-changes prompt, discards the autosave: in
  both cases whatever it would have recovered is now either superseded or
  explicitly unwanted.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from penultimate_cut.commands.timeline_commands import SetFitModeCommand
from penultimate_cut.io.autosave import (
    discard_autosave,
    find_recoverable_autosave,
    load_autosave,
    write_autosave,
)
from penultimate_cut.io.otio_io import (
    LoadResult,
    find_relink_candidates,
    load_project,
    save_project,
)
from penultimate_cut.model.project import Clip, FitMode, MediaRef, Project, Track, default_clip_for
from penultimate_cut.ui.clip_properties import ClipPropertiesPanel
from penultimate_cut.ui.export_dialog import ExportDialog
from penultimate_cut.ui.media_bin import MediaBin, MediaBinEntry
from penultimate_cut.ui.preview_widget import PreviewWidget
from penultimate_cut.ui.timeline.widget import TimelineWidget

_PROJECT_FILE_FILTER = "Penultimate Cut project (*.otio)"
_AUTOSAVE_INTERVAL_MS = 60_000


class MainWindow(QMainWindow):
    def __init__(self, **preview_player_options: object) -> None:
        """`preview_player_options` are forwarded to the preview's `Player`
        -- see its own docstring; a test-only escape hatch, nothing in
        normal use passes any."""
        super().__init__()
        self.resize(1280, 800)

        self.project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
        self.undo_stack = QUndoStack(self)
        self._current_path: Path | None = None
        self._selected_index: int | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        self.media_bin = MediaBin()
        self.preview = PreviewWidget(**preview_player_options)  # type: ignore[arg-type]
        self.clip_properties = ClipPropertiesPanel()
        self.timeline = TimelineWidget()
        self.timeline.set_project(self.project, self.undo_stack)

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.addWidget(self.media_bin)
        top_split.addWidget(self.preview)
        top_split.addWidget(self.clip_properties)
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 3)
        top_split.setStretchFactor(2, 1)

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.timeline)
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)

        root_layout.addWidget(main_split)

        self._build_menu()

        self._import_button = QPushButton("Import")
        self._split_button = QPushButton("Split")
        self._delete_button = QPushButton("Delete")
        self._add_title_button = QPushButton("Add Title")
        self._export_button = QPushButton("Export")
        self._import_button.clicked.connect(self.media_bin.choose_files)
        self._split_button.clicked.connect(self.timeline.split_at_playhead)
        self._delete_button.clicked.connect(lambda: self.timeline.delete_selected(ripple=True))
        self._export_button.clicked.connect(self._open_export_dialog)

        button_row = QHBoxLayout()
        button_row.addWidget(self._import_button)
        button_row.addWidget(self._split_button)
        button_row.addWidget(self._delete_button)
        button_row.addWidget(self._add_title_button)
        button_row.addWidget(self._export_button)
        root_layout.addLayout(button_row)

        self.media_bin.media_double_clicked.connect(self._on_media_double_clicked)
        self.timeline.playhead_moved.connect(self.preview.seek)
        self.preview.position_changed.connect(self._on_preview_position_changed)
        self.undo_stack.indexChanged.connect(self._on_undo_stack_changed)
        self.timeline.selection_changed.connect(self._on_timeline_selection_changed)
        self.clip_properties.fit_mode_changed.connect(self._on_fit_mode_changed)

        self._init_shortcuts()
        self._update_window_title()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(_AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        recovered = self._maybe_recover_autosave(None)
        if recovered is not None:
            self._finish_loading_project(recovered, None, recovered=True)

    # --- menu / shortcuts ---------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        new_action = file_menu.addAction("New")
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        new_action.triggered.connect(self._new_project)

        open_action = file_menu.addAction("Open…")
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self._open_project)

        file_menu.addSeparator()

        save_action = file_menu.addAction("Save")
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self._save_project)

        save_as_action = file_menu.addAction("Save As…")
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_project_as)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("Exit")
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)

    def _init_shortcuts(self) -> None:
        shortcuts = [
            ("Space", self.preview.toggle_play_pause),
            ("S", self.timeline.split_at_playhead),
            ("Delete", lambda: self.timeline.delete_selected(ripple=True)),
            ("Shift+Delete", lambda: self.timeline.delete_selected(ripple=False)),
            ("Ctrl+Z", lambda: self.undo_stack.undo()),
            ("Ctrl+Y", lambda: self.undo_stack.redo()),
            ("+", self.timeline.zoom_in),
            ("-", self.timeline.zoom_out),
        ]
        self._shortcuts = [QShortcut(QKeySequence(key), self) for key, _ in shortcuts]
        for shortcut, (_key, handler) in zip(self._shortcuts, shortcuts, strict=True):
            shortcut.activated.connect(handler)

    # --- File menu actions -------------------------------------------------------

    def _new_project(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        fresh = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
        self._load_fresh_project(fresh, path=None)
        self.media_bin.clear()

    def _open_project(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(Path.home()), _PROJECT_FILE_FILTER
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            result = load_project(path)
        except Exception as exc:  # noqa: BLE001 -- any failure here must become a dialog, not a crash
            QMessageBox.critical(self, "Couldn't open project", str(exc))
            return

        recovered = self._maybe_recover_autosave(path)
        self._finish_loading_project(
            recovered if recovered is not None else result, path, recovered=recovered is not None
        )

    def _save_project(self) -> bool:
        """Returns True if the project ended up saved (possibly after the
        user picked a location via Save As), False if they cancelled or the
        save failed -- callers (e.g. the unsaved-changes prompt) use this to
        decide whether it's safe to proceed with whatever they were about
        to do."""
        if self._current_path is None:
            return self._save_project_as()
        try:
            save_project(self.project, self._current_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Couldn't save project", str(exc))
            return False
        discard_autosave(self._current_path)
        self.undo_stack.setClean()
        self._update_window_title()
        return True

    def _save_project_as(self) -> bool:
        default_path = self._current_path or (Path.home() / "Untitled.otio")
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save project", str(default_path), _PROJECT_FILE_FILTER
        )
        if not path_str:
            return False
        path = Path(path_str)
        if path.suffix.lower() != ".otio":
            path = path.with_suffix(".otio")
        previous_path = self._current_path
        self._current_path = path
        saved = self._save_project()
        if saved and previous_path != path:
            # Whatever autosave existed at the old location (the fixed
            # "never saved yet" slot, or a different file entirely) is now
            # orphaned -- this content has a new home.
            discard_autosave(previous_path)
        return saved

    # --- relink ------------------------------------------------------------------

    def _offer_relink(self, missing: list[MediaRef]) -> None:
        names = "\n".join(f"• {media_ref.path.name}" for media_ref in missing)
        choice = QMessageBox.question(
            self,
            "Missing media",
            f"{len(missing)} file(s) used by this project couldn't be found:\n\n"
            f"{names}\n\nLook for them in a folder?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        folder_str = QFileDialog.getExistingDirectory(self, "Folder to search")
        if not folder_str:
            return
        matches = find_relink_candidates(missing, Path(folder_str))
        for media_ref in missing:
            found = matches.get(media_ref.id)
            if found is not None:
                media_ref.path = found
        QMessageBox.information(
            self, "Relink results", f"Found {len(matches)} of {len(missing)} file(s)."
        )

    # --- unsaved-changes guard ----------------------------------------------------

    def _confirm_discard_if_dirty(self) -> bool:
        """True if it's fine to proceed (nothing unsaved, or the user chose
        to save or explicitly discard); False if they cancelled, meaning the
        caller should abandon whatever it was about to do."""
        if self.undo_stack.isClean():
            return True
        choice = QMessageBox.warning(
            self,
            "Unsaved changes",
            "This project has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_project()
        # Discard: the user explicitly doesn't want this work recovered
        # later either.
        discard_autosave(self._current_path)
        return True

    # --- shared project-switching plumbing -----------------------------------------

    def _load_fresh_project(self, project: Project, path: Path | None) -> None:
        self.undo_stack.indexChanged.disconnect(self._on_undo_stack_changed)
        self.project = project
        self.undo_stack = QUndoStack(self)
        self.undo_stack.indexChanged.connect(self._on_undo_stack_changed)
        self._current_path = path
        self._selected_index = None
        self.timeline.set_project(self.project, self.undo_stack)
        self.clip_properties.set_clip(None)
        self.undo_stack.setClean()
        self._update_window_title()

    def _finish_loading_project(
        self, result: LoadResult, path: Path | None, *, recovered: bool
    ) -> None:
        if result.missing_media:
            self._offer_relink(result.missing_media)
        self._load_fresh_project(result.project, path)
        self.media_bin.clear()
        self._populate_media_bin_from_project(result.project)
        if recovered:
            # Recovered content was never actually written to `path` (or
            # has no path at all) -- leave it dirty so the usual save
            # prompts protect it, instead of _load_fresh_project's usual
            # setClean() implying it's already safe on disk.
            self.undo_stack.push(QUndoCommand("Recovered autosave"))

    def _maybe_recover_autosave(self, path: Path | None) -> LoadResult | None:
        autosave_path = find_recoverable_autosave(path)
        if autosave_path is None:
            return None
        choice = QMessageBox.question(
            self,
            "Recover autosave",
            "A more recent autosave was found for this project. Recover it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            discard_autosave(path)
            return None
        try:
            return load_autosave(autosave_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Couldn't recover autosave", str(exc))
            return None

    def _autosave(self) -> None:
        if self.undo_stack.isClean():
            return
        try:
            write_autosave(self.project, self._current_path)
        except OSError:
            pass  # best-effort; a dialog every 60s for a transient disk issue would be worse

    def _populate_media_bin_from_project(self, project: Project) -> None:
        seen: set[str] = set()
        for track in project.tracks:
            for item in track.items:
                if isinstance(item, Clip) and item.media_ref.id not in seen:
                    seen.add(item.media_ref.id)
                    self.media_bin.add_media_ref(item.media_ref)

    def _update_window_title(self) -> None:
        name = self._current_path.name if self._current_path else "Untitled"
        dirty_marker = "" if self.undo_stack.isClean() else "*"
        self.setWindowTitle(f"{dirty_marker}{name} — Penultimate Cut")

    # --- other wiring --------------------------------------------------------------

    def _on_media_double_clicked(self, entry: MediaBinEntry) -> None:
        self.timeline.insert_clip(default_clip_for(entry.media_ref))

    def _on_preview_position_changed(self, position: Fraction | None) -> None:
        if position is not None:
            self.timeline.set_playhead(position)

    def _on_undo_stack_changed(self, _index: int) -> None:
        if self.project.duration > 0:
            self.preview.load_project(self.project)
        self._refresh_clip_properties()
        self._update_window_title()

    def _on_timeline_selection_changed(self, index: int | None) -> None:
        self._selected_index = index
        self._refresh_clip_properties()

    def _on_fit_mode_changed(self, fit_mode: FitMode) -> None:
        if self._selected_index is None:
            return
        self.undo_stack.push(SetFitModeCommand(self.project, 0, self._selected_index, fit_mode))

    def _open_export_dialog(self) -> None:
        self._autosave()
        ExportDialog(self.project, self).exec()

    def _refresh_clip_properties(self) -> None:
        """Re-derive the displayed clip from `_selected_index` and the
        project's current state -- needed after any edit, since e.g. undoing
        a trim swaps in a different clip object at the same index, and a
        delete or an edit elsewhere can make the index itself stale."""
        clip = None
        items = self.project.tracks[0].items
        if self._selected_index is not None and 0 <= self._selected_index < len(items):
            item = items[self._selected_index]
            clip = item if isinstance(item, Clip) else None
        else:
            self._selected_index = None
        self.clip_properties.set_clip(clip)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard_if_dirty():
            event.ignore()
            return
        self._autosave_timer.stop()
        self.media_bin.shutdown()
        self.timeline.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
