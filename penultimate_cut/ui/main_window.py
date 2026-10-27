"""Main window: preview (top), media bin (left), timeline (bottom).

Owns the one `Project` and `QUndoStack` for the open document and wires the
real widgets together:

- Media bin double-click -> a default `Clip` for that media, appended to
  the timeline via an `InsertCommand`. Dragging a media bin entry onto the
  timeline does the same thing, landing at wherever was dropped instead of
  always the end -- `TimelineWidget.media_dropped` carries the media's id
  (not a `MediaRef` itself: the timeline widget has no reason to know
  about the media bin's entries) and the drop index it already computed
  from the drop position.
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
  why the Edit menu's Undo/Redo actions are wrapped in lambdas rather than
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

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QIcon,
    QKeySequence,
    QShortcut,
    QUndoCommand,
    QUndoStack,
)
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

from penultimate_cut.commands.timeline_commands import RemoveMediaCommand, SetFitModeCommand
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
from penultimate_cut.ui.format import format_speed
from penultimate_cut.ui.media_bin import MediaBin, MediaBinEntry
from penultimate_cut.ui.preview_widget import PLAYBACK_SPEEDS, PreviewWidget
from penultimate_cut.ui.settings import (
    default_folder,
    load_preview_volume,
    save_preview_volume,
    unique_path,
)
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

        # The edit buttons sit in a slim bar on top of the timeline they act on.
        Icon = QIcon.ThemeIcon
        self._import_button = _toolbar_button(Icon.DocumentOpen, "Import", "Import media (Ctrl+I)")
        self._split_button = _toolbar_button(Icon.EditCut, "Split", "Split at the playhead (S)")
        self._delete_button = _toolbar_button(
            Icon.EditDelete,
            "Delete",
            "Delete the selected clip, leaving empty space (Delete).\n"
            "Shift+Delete also closes the gap.",
        )
        self._add_title_button = _toolbar_button(Icon.InsertText, "Add Title", "Add a title")
        self._add_title_button.hide()  # titles are Phase 2; nothing to do yet
        self._export_button = _toolbar_button(
            Icon.DocumentSaveAs, "Export", "Make the finished video file (Ctrl+E)"
        )
        self._import_button.clicked.connect(self.media_bin.choose_files)
        self._split_button.clicked.connect(self.timeline.split_at_playhead)
        self._delete_button.clicked.connect(lambda: self.timeline.delete_selected(ripple=False))
        self._export_button.clicked.connect(self._open_export_dialog)

        button_bar = QHBoxLayout()
        button_bar.setContentsMargins(0, 0, 0, 2)
        button_bar.addWidget(self._import_button)
        button_bar.addWidget(self._split_button)
        button_bar.addWidget(self._delete_button)
        button_bar.addWidget(self._add_title_button)
        button_bar.addStretch(1)
        button_bar.addWidget(self._export_button)
        timeline_pane = QWidget()
        timeline_layout = QVBoxLayout(timeline_pane)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)
        timeline_layout.addLayout(button_bar)
        timeline_layout.addWidget(self.timeline)
        # One track needs no more height than this; the preview gets the rest.
        # (Phase 2's extra tracks will need this to grow.)
        timeline_pane.setMaximumHeight(timeline_pane.sizeHint().height())

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(timeline_pane)
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)

        root_layout.addWidget(main_split)

        self._build_menu()

        self.media_bin.media_double_clicked.connect(self._on_media_double_clicked)
        self.media_bin.remove_requested.connect(self._on_media_remove_requested)
        self.timeline.media_dropped.connect(self._on_media_dropped)
        self.timeline.playhead_moved.connect(self.preview.seek)
        self.preview.position_changed.connect(self._on_preview_position_changed)
        self.preview.set_volume(load_preview_volume())
        self.preview.volume_changed.connect(save_preview_volume)
        self.undo_stack.indexChanged.connect(self._on_undo_stack_changed)
        self.timeline.selection_changed.connect(self._on_timeline_selection_changed)
        self.clip_properties.fit_mode_changed.connect(self._on_fit_mode_changed)

        self._init_shortcuts()
        self._update_window_title()
        self._update_edit_actions()

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

        import_action = file_menu.addAction("Import Media…")
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(self.media_bin.choose_files)

        file_menu.addSeparator()

        save_action = file_menu.addAction("Save")
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self._save_project)

        save_as_action = file_menu.addAction("Save As…")
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_project_as)

        file_menu.addSeparator()

        export_action = file_menu.addAction("Export Video…")
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._open_export_dialog)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("Exit")
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)

        # Plain actions calling whatever `self.undo_stack` is *now*, not
        # QUndoStack.createUndoAction(): that binds to one stack object, and
        # New/Open replace the stack (same reason as the lambdas in
        # _init_shortcuts). `_update_edit_actions` keeps text/enabled current.
        edit_menu = self.menuBar().addMenu("&Edit")
        self._undo_action = edit_menu.addAction("Undo")
        self._undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self._undo_action.triggered.connect(lambda: self.undo_stack.undo())
        self._redo_action = edit_menu.addAction("Redo")
        self._redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        self._redo_action.triggered.connect(lambda: self.undo_stack.redo())
        edit_menu.addSeparator()
        # Both act at the playhead. Split stays enabled (it's a no-op off a
        # clip): its S key must work even if the menu's state is stale.
        # Delete Clip greys out, refreshed whenever the menu opens; it has no
        # Delete shortcut because the Delete key is a shortcut of its own that
        # acts on the media bin *or* the timeline (see _on_delete_key).
        self._split_action = edit_menu.addAction("Split")
        self._split_action.setShortcut(QKeySequence("S"))
        self._split_action.triggered.connect(self.timeline.split_at_playhead)
        self._delete_clip_action = edit_menu.addAction("Delete Clip")
        self._delete_clip_action.triggered.connect(self.timeline.delete_clip_at_playhead)
        self._close_gap_action = edit_menu.addAction("Delete and Close Gap")
        self._close_gap_action.triggered.connect(
            lambda: self.timeline.delete_clip_at_playhead(close_gap=True)
        )
        edit_menu.aboutToShow.connect(self._update_edit_actions)

        self._build_play_menu()

    def _build_play_menu(self) -> None:
        play_menu = self.menuBar().addMenu("&Play")

        def add(text: str, key: str, handler: Callable[[], None]) -> QAction:
            action = play_menu.addAction(text)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(handler)
            return action

        add("Play / Pause", "Space", self.preview.toggle_play_pause)
        add("Faster", "L", self.preview.faster)

        speed_menu = play_menu.addMenu("Playback Speed")
        speed_group = QActionGroup(self)
        self._speed_actions: dict[Fraction, QAction] = {}
        for speed in PLAYBACK_SPEEDS:
            label = "Normal (1×)" if speed == 1 else format_speed(speed)
            action = speed_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(speed == 1)
            speed_group.addAction(action)
            action.triggered.connect(lambda _checked=False, s=speed: self.preview.play_at_speed(s))
            self._speed_actions[speed] = action
        self.preview.speed_changed.connect(lambda s: self._speed_actions[s].setChecked(True))

        play_menu.addSeparator()
        add("Next Frame", "Right", lambda: self.preview.frame_step("forward"))
        add("Previous Frame", "Left", lambda: self.preview.frame_step("backward"))
        add("Forward 1 Second", "Shift+Right", lambda: self._jump_by(Fraction(1)))
        add("Back 1 Second", "Shift+Left", lambda: self._jump_by(Fraction(-1)))
        play_menu.addSeparator()
        add("Go to Start", "Home", lambda: self._jump_to(Fraction(0)))
        add("Go to End", "End", lambda: self._jump_to(self.project.duration))

    def _jump_by(self, delta: Fraction) -> None:
        self._jump_to(self.timeline.playhead + delta)

    def _jump_to(self, position: Fraction) -> None:
        position = max(Fraction(0), min(position, self.project.duration))
        self.timeline.set_playhead(position)
        self.preview.seek(position)

    def _init_shortcuts(self) -> None:
        # Everything else has its key on a menu action (so the menus show
        # it); these have no menu item of their own.
        shortcuts = [
            ("Delete", self._on_delete_key),
            ("Shift+Delete", lambda: self.timeline.delete_selected(ripple=True)),
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
            self, "Open project", str(self._project_folder()), _PROJECT_FILE_FILTER
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
        default_path = self._current_path or (default_folder() / "Untitled.otio")
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
        self.timeline.set_playhead(Fraction(0))
        self._reload_preview()
        self.clip_properties.set_clip(None)
        self.undo_stack.setClean()
        self._update_window_title()
        self._update_edit_actions()

    def _reload_preview(self) -> None:
        if self.project.duration > 0:
            # Keep the preview where the playhead is; a reload otherwise
            # starts from 0:00 and drags the timeline's playhead with it.
            self.preview.load_project(self.project, start=self.timeline.playhead)
        else:
            self.preview.clear()  # not still showing the last deleted clip

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

    def _update_edit_actions(self) -> None:
        for action, verb, available, text in (
            (self._undo_action, "Undo", self.undo_stack.canUndo(), self.undo_stack.undoText()),
            (self._redo_action, "Redo", self.undo_stack.canRedo(), self.undo_stack.redoText()),
        ):
            action.setEnabled(available)
            action.setText(f"{verb} {text}" if available and text else verb)
        on_clip = self.timeline.clip_index_at_playhead() is not None
        self._delete_clip_action.setEnabled(on_clip)
        self._close_gap_action.setEnabled(on_clip)

    # --- other wiring --------------------------------------------------------------

    def _on_media_double_clicked(self, entry: MediaBinEntry) -> None:
        self.timeline.insert_clip(default_clip_for(entry.media_ref))

    def _on_media_dropped(self, media_id: str, index: int) -> None:
        entry = self.media_bin.get(media_id)
        if entry is not None:
            self.timeline.insert_clip(default_clip_for(entry.media_ref), index=index)

    def _on_preview_position_changed(self, position: Fraction | None) -> None:
        if position is not None:
            self.timeline.set_playhead(position)

    def _on_undo_stack_changed(self, _index: int) -> None:
        self._reload_preview()
        self._refresh_clip_properties()
        self._update_window_title()
        self._update_edit_actions()
        # Undoing a "Remove media" brings its clips back; their media must
        # come back to the bin too. (A no-op for media already there.)
        self._populate_media_bin_from_project(self.project)

    def _on_delete_key(self) -> None:
        if self.media_bin.has_focus():
            self.media_bin.request_remove_selected()
        else:
            self.timeline.delete_selected(ripple=False)

    def _on_media_remove_requested(self, entry: MediaBinEntry) -> None:
        """Unused media goes silently. Media with clips on the timeline asks
        first -- those clips may be off-screen, so removing them wouldn't be
        obvious -- then removes them as one undoable edit."""
        media_id = entry.media_ref.id
        clip_count = sum(
            1
            for track in self.project.tracks
            for item in track.items
            if isinstance(item, Clip) and item.media_ref.id == media_id
        )
        if clip_count and not self._confirm_remove_used_media(
            entry.media_ref.path.name, clip_count
        ):
            return
        self.media_bin.remove(media_id)
        if clip_count:
            self.undo_stack.push(RemoveMediaCommand(self.project, media_id))

    def _confirm_remove_used_media(self, name: str, clip_count: int) -> bool:
        plural = "clip" if clip_count == 1 else "clips"
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Remove media",
            f"“{name}” is used by {clip_count} {plural} on the timeline. "
            f"Removing it also removes the {plural}.\n\n"
            "You can undo this with Edit → Undo.",
            QMessageBox.StandardButton.Cancel,
            self,
        )
        remove_button = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is remove_button

    def _on_timeline_selection_changed(self, index: int | None) -> None:
        self._selected_index = index
        self._refresh_clip_properties()

    def _on_fit_mode_changed(self, fit_mode: FitMode) -> None:
        if self._selected_index is None:
            return
        self.undo_stack.push(SetFitModeCommand(self.project, 0, self._selected_index, fit_mode))

    def _open_export_dialog(self) -> None:
        self._autosave()
        ExportDialog(self.project, self._default_export_path(), self).exec()

    def _project_folder(self) -> Path:
        """The current project's folder, or the app's default folder for a
        project that hasn't been saved yet."""
        if self._current_path is not None:
            return self._current_path.parent
        return default_folder()

    def _default_export_path(self) -> Path:
        """`<project name>.mp4` next to the project -- never an existing
        file, so accepting the default can't silently overwrite an earlier
        export."""
        stem = self._current_path.stem if self._current_path is not None else "Untitled"
        return unique_path(self._project_folder() / f"{stem}.mp4")

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


def _toolbar_button(icon: QIcon.ThemeIcon, text: str, tooltip: str) -> QPushButton:
    """A compact button with an icon from Windows' own icon font (via Qt's
    theme icons) and a short label. No keyboard focus, so Space keeps
    meaning play/pause after a click instead of pressing the button again."""
    button = QPushButton(QIcon.fromTheme(icon), text)
    button.setIconSize(QSize(18, 18))
    button.setToolTip(tooltip)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button
