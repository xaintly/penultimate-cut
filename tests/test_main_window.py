import time
from fractions import Fraction

from penultimate_cut.model.project import Clip, Gap
from penultimate_cut.ui.main_window import MainWindow


def pump_until(app, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_end_to_end_editing_flow(qapp, make_video_clip) -> None:
    """One flowing scenario, deliberately a single test function rather than
    several, covering the wiring this session built: construction -> import
    -> add to timeline (double-click, then later drag-drop) -> preview
    loads -> split -> delete -> undo/redo -> zoom -> shortcuts registered.

    Each of MainWindow's three real widgets owns a long-lived resource (the
    media bin's thread pool, the timeline's, and above all the preview's
    embedded mpv `Player`), and constructing/tearing down a *second*
    `MainWindow` (so a *second* embedded Player) in the same process is the
    same reliable hang already worked around in test_preview_widget.py --
    so this file has exactly one `MainWindow()` for all of it, not one per
    test function.

    Deliberately only one wait-for-reload (after the initial add): that's
    what proves QUndoStack.indexChanged -> preview reload is wired up at
    all. split/delete/undo/redo are then checked at the model level only
    (item counts, undo_stack state), without an mpv-reload wait after each
    -- those are plain, instant, non-mpv assertions, and *that* specific
    rapid-reload-then-immediately-seek sequence is what turned out to be
    genuinely flaky in this test environment (reproduced intermittently
    even after fixing the real bug it surfaced in Player, a bare SystemError
    from a transient mpv command error -- see preview/player.py). The two
    reload cycles this scenario still does (initial load, each counts as
    one) are well short of whatever that flakiness's actual threshold is;
    the fully isolated two-reload case is already covered reliably by
    test_player.py's test_reloading_edl_reuses_the_same_scratch_file.
    """
    source = make_video_clip("mw_e2e.mp4", duration=4.0, fps=30, size="320x240")
    # vo="null": this test is about signal wiring, not rendering (that's
    # test_preview_widget.py's job, with its own real GL embedding). Doing
    # several embedded-mpv reloads back-to-back with a real GL context under
    # this environment's software (llvmpipe) renderer turned out to
    # intermittently crash the Xvfb server itself -- an infrastructure
    # limitation of this specific headless/software-GL combination, not a
    # bug in the app, and side-stepped entirely by not creating a GL context
    # here at all.
    window = MainWindow(vo="null")
    try:
        window.show()
        qapp.processEvents()

        assert window.media_bin is not None
        assert window.preview is not None
        assert window.timeline is not None
        assert window.clip_properties is not None
        assert len(window.project.tracks) == 1
        # Only Delete, Shift+Delete, +, - are plain shortcuts; every other
        # key lives on a menu action, so the menus show it.
        assert len(window._shortcuts) == 4
        menu_keys = {
            action.text(): action.shortcut().toString()
            for menu_action in window.menuBar().actions()
            for action in menu_action.menu().actions()
            if not action.shortcut().isEmpty()
        }
        assert menu_keys["Split"] == "S"
        assert menu_keys["Play / Pause"] == "Space"
        assert menu_keys["Faster"] == "L"
        assert menu_keys["Next Frame"] == "Right"
        assert menu_keys["Back 1 Second"] == "Shift+Left"
        assert menu_keys["Go to Start"] == "Home"
        assert menu_keys["Go to End"] == "End"
        assert window._add_title_button.isHidden()  # Phase 2
        # Undo/Redo live in the Edit menu (with their Ctrl+Z/Ctrl+Y
        # shortcuts), disabled while there's nothing to undo/redo.
        assert window._undo_action.shortcut().toString() == "Ctrl+Z"
        assert window._redo_action.shortcut().toString() == "Ctrl+Y"
        assert not window._undo_action.isEnabled()
        # Not clicked: opens a modal ExportDialog, already tested on its own
        # in test_export_dialog.py.
        assert window._export_button.isEnabled()

        # Import -> double-click adds it to the timeline -> preview loads.
        window.media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(window.media_bin.entries) == 1)
        entry = window.media_bin.entries[0]
        window.media_bin.media_double_clicked.emit(entry)
        qapp.processEvents()

        assert len(window.project.tracks[0].items) == 1
        assert window.project.tracks[0].items[0].media_ref.path == source
        assert window.undo_stack.count() == 1
        assert pump_until(qapp, lambda: window.preview.player.duration is not None)
        assert window.preview.player.duration == Fraction(4)

        # Timeline selection -> clip properties panel, and the panel's own
        # Fit Mode control -> a SetFitModeCommand for that clip.
        window.timeline._canvas._select(0)
        qapp.processEvents()
        assert window.clip_properties._clip is window.project.tracks[0].items[0]
        assert window.clip_properties._fit_mode_combo.isEnabled()

        blurred_index = window.clip_properties._fit_mode_combo.findData("blurred_background")
        window.clip_properties._fit_mode_combo.setCurrentIndex(blurred_index)
        assert window.project.tracks[0].items[0].fit_mode == "blurred_background"
        assert window.undo_stack.count() == 2

        window.undo_stack.undo()
        assert window.project.tracks[0].items[0].fit_mode == "black_bars"
        # Undo swapped in a different (restored) clip object at the same
        # index -- the panel should be showing *that* one now, not the
        # stale pre-undo object.
        assert window.clip_properties._clip is window.project.tracks[0].items[0]

        window.undo_stack.redo()
        assert window.project.tracks[0].items[0].fit_mode == "blurred_background"

        # Split button, at the playhead.
        window.timeline.set_playhead(Fraction(2))
        window._split_button.click()
        assert len(window.project.tracks[0].items) == 2
        assert window.undo_stack.count() == 3

        # Delete button, on the selected (first) piece: leaves empty space
        # behind (Movie Maker style), rather than closing the gap.
        window.timeline._canvas._select(0)
        window._delete_button.click()
        assert len(window.project.tracks[0].items) == 2
        assert isinstance(window.project.tracks[0].items[0], Gap)
        assert window.project.tracks[0].items[0].duration == Fraction(2)
        assert window.undo_stack.count() == 4

        # Undo/redo via the stack directly (the shortcuts just call these
        # same methods; already checked structurally above).
        window.undo_stack.undo()
        assert isinstance(window.project.tracks[0].items[0], Clip)
        window.undo_stack.redo()
        assert isinstance(window.project.tracks[0].items[0], Gap)

        # Deleting the selected empty space closes it.
        window.timeline._canvas._select(0)
        window._delete_button.click()
        assert len(window.project.tracks[0].items) == 1
        assert window.project.tracks[0].items[0].duration == Fraction(2)
        assert window.undo_stack.count() == 5

        # Edit > Delete Clip acts on the clip under the playhead; greyed out
        # when the playhead isn't over one (checked as the menu opens).
        window.timeline.set_playhead(Fraction(2))  # the end: no clip there
        window._update_edit_actions()
        assert not window._delete_clip_action.isEnabled()
        window.timeline.set_playhead(Fraction(1))
        window._update_edit_actions()
        assert window._delete_clip_action.isEnabled()
        window._delete_clip_action.trigger()
        assert window.project.tracks[0].items == []
        window.undo_stack.undo()
        assert len(window.project.tracks[0].items) == 1
        # Edit > Delete and Close Gap: the same, greyed out the same way.
        assert window._close_gap_action.isEnabled()
        window._close_gap_action.trigger()
        assert window.project.tracks[0].items == []
        window.undo_stack.undo()

        file_actions = {
            action.text(): action
            for menu_action in window.menuBar().actions()
            if menu_action.text() == "&File"
            for action in menu_action.menu().actions()
        }
        assert file_actions["Import Media…"].shortcut().toString() == "Ctrl+I"

        # Play menu jumps move the playhead, clamped to the timeline.
        play_actions = {
            action.text(): action
            for menu_action in window.menuBar().actions()
            if menu_action.text() == "&Play"
            for action in menu_action.menu().actions()
        }
        play_actions["Go to End"].trigger()
        assert window.timeline.playhead == window.project.duration == Fraction(2)
        play_actions["Forward 1 Second"].trigger()
        assert window.timeline.playhead == Fraction(2)
        play_actions["Back 1 Second"].trigger()
        assert window.timeline.playhead == Fraction(1)
        play_actions["Go to Start"].trigger()
        assert window.timeline.playhead == 0
        play_actions["Back 1 Second"].trigger()
        assert window.timeline.playhead == 0

        # Dragging a media bin entry onto the timeline: same as
        # double-click, but landing at a specific index instead of always
        # the end.
        window.timeline.media_dropped.emit(entry.media_ref.id, 0)
        assert len(window.project.tracks[0].items) == 2
        assert window.project.tracks[0].items[0].duration == Fraction(4)  # the whole new clip
        assert window.project.tracks[0].items[1].duration == Fraction(2)  # the pre-existing one

        # Zoom.
        baseline = window.timeline._canvas._view.pixels_per_second
        window.timeline.zoom_in()
        assert window.timeline._canvas._view.pixels_per_second > baseline
    finally:
        # This scenario deliberately leaves the undo stack dirty (it never
        # saves) -- mark it clean first so close() doesn't block on the
        # real unsaved-changes prompt test_file_menu.py exercises on its own.
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()


def test_removing_media_from_the_bin(qapp, make_video_clip, monkeypatch) -> None:
    used = make_video_clip("mw_used.mp4", duration=2.0, fps=30, size="320x240")
    unused = make_video_clip("mw_unused.mp4", duration=2.0, fps=30, size="320x240")
    window = MainWindow(vo="null")
    try:
        window.media_bin.import_files([used, unused])
        assert pump_until(qapp, lambda: len(window.media_bin.entries) == 2)
        by_name = {e.media_ref.path.name: e for e in window.media_bin.entries}
        used_entry, unused_entry = by_name["mw_used.mp4"], by_name["mw_unused.mp4"]
        window._on_media_double_clicked(used_entry)
        asked: list[int] = []

        def answer(result: bool):
            def confirm(_name: str, count: int) -> bool:
                asked.append(count)
                return result

            return confirm

        # Not on the timeline: removed silently, nothing to undo.
        monkeypatch.setattr(window, "_confirm_remove_used_media", answer(True))
        window._on_media_remove_requested(unused_entry)
        assert asked == []
        assert window.media_bin.get(unused_entry.media_ref.id) is None
        assert window.undo_stack.count() == 1  # just the insert

        # On the timeline, and the user cancels: nothing changes.
        monkeypatch.setattr(window, "_confirm_remove_used_media", answer(False))
        window._on_media_remove_requested(used_entry)
        assert asked == [1]
        assert window.media_bin.get(used_entry.media_ref.id) is not None
        assert len(window.project.tracks[0].items) == 1

        # Confirmed: media and its clip go, as one undoable edit...
        monkeypatch.setattr(window, "_confirm_remove_used_media", answer(True))
        window._on_media_remove_requested(used_entry)
        assert window.media_bin.get(used_entry.media_ref.id) is None
        assert window.project.tracks[0].items == []
        assert window._undo_action.text() == "Undo Remove media"

        # ...and undoing it brings the media back to the bin with the clip.
        window._undo_action.trigger()
        assert len(window.project.tracks[0].items) == 1
        assert pump_until(qapp, lambda: window.media_bin.get(used_entry.media_ref.id) is not None)
    finally:
        window.undo_stack.setClean()
        window.close()
        qapp.processEvents()
