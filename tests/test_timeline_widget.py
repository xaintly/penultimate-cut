import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QUndoStack
from PySide6.QtTest import QTest

from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Track
from penultimate_cut.ui.media_bin import MEDIA_MIME_TYPE
from penultimate_cut.ui.timeline.widget import (
    _CLIP_BG,
    _PLAYHEAD_COLOR,
    _TRACK_BG,
    TRACK_TOP,
    TimelineWidget,
    _grid_seconds,
    _tile_source_time,
    _tile_width,
)


def make_media(source, duration: float = 10.0) -> MediaRef:
    return MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(str(duration)),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=320,
        height=240,
    )


def make_project(source, duration: float = 10.0) -> Project:
    clip = Clip(
        media_ref=make_media(source, duration),
        source_in=Fraction(0),
        source_out=Fraction(str(duration)),
    )
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])


def pump_until(app, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_paints_without_a_project(qapp) -> None:
    widget = TimelineWidget()
    try:
        widget.resize(400, 150)
        widget.show()
        qapp.processEvents()
        pixmap = widget.grab()
        assert not pixmap.isNull()
    finally:
        widget.shutdown()


def test_renders_clip_background_track_background_and_playhead(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_pixels.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.set_playhead(Fraction(1))
        widget.show()
        qapp.processEvents()

        image = widget._canvas.grab().toImage()
        empty_area = image.pixelColor(700, TRACK_TOP + 40)
        clip_area = image.pixelColor(20, TRACK_TOP + 40)
        playhead_x = int(widget._canvas._view.x_for_time(Fraction(1)))
        playhead_pixel = image.pixelColor(playhead_x, 5)

        assert empty_area.name() == _TRACK_BG.name()
        assert clip_area.name() == _CLIP_BG.name()
        assert playhead_pixel.name() == _PLAYHEAD_COLOR.name()
    finally:
        widget.shutdown()


def test_click_selects_clip_and_moves_playhead(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_click.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        positions: list[Fraction] = []
        selections: list[int | None] = []
        widget.playhead_moved.connect(positions.append)
        widget.selection_changed.connect(selections.append)

        # 100px at the default 50px/s -> 2.0s.
        QTest.mouseClick(
            widget._canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(100, TRACK_TOP + 10),
        )
        qapp.processEvents()

        assert positions == [Fraction(2)]
        assert selections == [0]
    finally:
        widget.shutdown()


def test_click_past_the_end_deselects_and_scrubs(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_pastend.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        widget._canvas._select(0)
        selections: list[int | None] = []
        widget.selection_changed.connect(selections.append)

        QTest.mouseClick(
            widget._canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(700, TRACK_TOP + 10),
        )
        qapp.processEvents()

        assert selections == [None]
    finally:
        widget.shutdown()


def test_trim_drag_commits_one_undo_step(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_trim.mp4", duration=10.0, fps=30, size="320x240")
    project = make_project(source, duration=10.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        canvas = widget._canvas
        y = TRACK_TOP + 10
        # 10s clip at 50px/s spans 0..500px; grab the right trim handle near
        # x=495 and drag it 100px (2s) left.
        QTest.mousePress(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(495, y)
        )
        QTest.mouseMove(canvas, QPoint(395, y))
        QTest.mouseRelease(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(395, y)
        )
        qapp.processEvents()

        assert stack.count() == 1
        assert project.tracks[0].items[0].source_out == Fraction(8)

        stack.undo()
        assert project.tracks[0].items[0].source_out == Fraction(10)
    finally:
        widget.shutdown()


def test_trim_moves_by_mouse_delta_in_source_time(qapp, make_video_clip) -> None:
    # Regression: a clip starting 5s into its source sits at timeline 0:00.
    # Clicking its left edge without dragging used to trim its start to
    # *source* time 0 (the timeline time under the mouse), bringing back
    # the whole deleted first half.
    source = make_video_clip("tlw_trim_offset.mp4", duration=10.0, fps=30, size="320x240")
    clip = Clip(media_ref=make_media(source), source_in=Fraction(5), source_out=Fraction(10))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        canvas = widget._canvas
        y = TRACK_TOP + 10
        left, right = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        QTest.mousePress(canvas, left, right, QPoint(2, y))
        QTest.mouseRelease(canvas, left, right, QPoint(2, y))
        qapp.processEvents()
        assert stack.count() == 0
        assert project.tracks[0].items[0].source_in == Fraction(5)

        # Dragging the start handle 50px (1s) right trims 1s off the start.
        QTest.mousePress(canvas, left, right, QPoint(2, y))
        QTest.mouseMove(canvas, QPoint(52, y))
        QTest.mouseRelease(canvas, left, right, QPoint(52, y))
        qapp.processEvents()
        assert stack.count() == 1
        assert project.tracks[0].items[0].source_in == Fraction(6)
    finally:
        widget.shutdown()


def test_delete_all_gaps(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_gaps.mp4", duration=10.0, fps=30, size="320x240")
    media = make_media(source)
    clip_a = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    clip_b = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    items = [Gap(duration=Fraction(2)), clip_a, Gap(duration=Fraction(1)), clip_b]
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        widget._canvas.delete_gap(2)
        assert project.tracks[0].items == [items[0], clip_a, clip_b]
        widget._canvas.delete_all_gaps()
        assert project.tracks[0].items == [clip_a, clip_b]
        assert stack.count() == 2
    finally:
        widget.shutdown()


def test_playhead_leaving_view_pages_the_timeline(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_autoscroll.mp4", duration=30.0, fps=30, size="320x240")
    project = make_project(source, duration=30.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(400, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        # 400px at 50px/s shows 0..8s.
        widget.set_playhead(Fraction(5))
        assert widget._canvas._view.scroll_offset == 0
        widget.set_playhead(Fraction(9))
        assert widget._canvas._view.scroll_offset == Fraction(9)
        widget.set_playhead(Fraction(1))
        assert widget._canvas._view.scroll_offset == Fraction(1)
    finally:
        widget.shutdown()


def test_reorder_drag_commits_one_undo_step(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_reorder.mp4", duration=10.0, fps=30, size="320x240")
    media = make_media(source)
    clip_a = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    clip_b = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip_a, clip_b])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        canvas = widget._canvas
        y = TRACK_TOP + 10
        # clip_a spans 0-150px, clip_b spans 150-300px at 50px/s.
        QTest.mousePress(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(75, y)
        )
        QTest.mouseMove(canvas, QPoint(90, y))
        QTest.mouseMove(canvas, QPoint(200, y))
        QTest.mouseRelease(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(200, y)
        )
        qapp.processEvents()

        assert stack.count() == 1
        assert project.tracks[0].items == [clip_b, clip_a]

        stack.undo()
        assert project.tracks[0].items == [clip_a, clip_b]
    finally:
        widget.shutdown()


def test_small_movement_on_a_clip_body_is_not_a_reorder(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_noop_drag.mp4", duration=10.0, fps=30, size="320x240")
    media = make_media(source)
    clip_a = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    clip_b = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip_a, clip_b])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        canvas = widget._canvas
        y = TRACK_TOP + 10
        QTest.mousePress(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(75, y)
        )
        QTest.mouseMove(canvas, QPoint(77, y))  # well under DRAG_THRESHOLD_PX
        QTest.mouseRelease(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(77, y)
        )
        qapp.processEvents()

        assert stack.count() == 0
        assert project.tracks[0].items == [clip_a, clip_b]
    finally:
        widget.shutdown()


def test_split_at_playhead(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_split.mp4", duration=10.0, fps=30, size="320x240")
    project = make_project(source, duration=10.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        widget.set_playhead(Fraction(4))
        widget.split_at_playhead()

        assert stack.count() == 1
        assert len(project.tracks[0].items) == 2
        assert project.tracks[0].items[0].duration == Fraction(4)
    finally:
        widget.shutdown()


def test_delete_selected_ripple(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_delete.mp4", duration=10.0, fps=30, size="320x240")
    media = make_media(source)
    clip_a = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    clip_b = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip_a, clip_b])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        widget._canvas._select(0)
        widget.delete_selected(ripple=True)

        assert stack.count() == 1
        assert project.tracks[0].items == [clip_b]
    finally:
        widget.shutdown()


def test_insert_clip_appends_at_end(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_insert.mp4", duration=5.0, fps=30, size="320x240")
    project = make_project(source, duration=5.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        new_clip = Clip(media_ref=make_media(source), source_in=Fraction(0), source_out=Fraction(2))
        widget.insert_clip(new_clip)

        assert stack.count() == 1
        assert project.tracks[0].items[-1] is new_clip
    finally:
        widget.shutdown()


def test_insert_gap_into_empty_track(qapp) -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        widget.insert_clip(Gap(duration=Fraction(5)))

        assert len(project.tracks[0].items) == 1
        assert isinstance(project.tracks[0].items[0], Gap)
    finally:
        widget.shutdown()


def test_zoom_in_and_out_change_pixels_per_second(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_zoom.mp4", duration=10.0, fps=30, size="320x240")
    project = make_project(source, duration=10.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        baseline = widget._canvas._view.pixels_per_second

        widget.zoom_in()
        assert widget._canvas._view.pixels_per_second > baseline

        widget.zoom_out()
        widget.zoom_out()
        assert widget._canvas._view.pixels_per_second < baseline

        assert stack.count() == 0  # zoom is not an undoable edit
    finally:
        widget.shutdown()


def test_scrollbar_range_tracks_zoom_and_content_width(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_scroll.mp4", duration=10.0, fps=30, size="320x240")
    project = make_project(source, duration=10.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(300, 150)  # narrower than 10s * 50px/s = 500px
        widget.show()
        widget.set_project(project, stack)
        qapp.processEvents()
        assert widget._scrollbar.maximum() > 0

        widget.zoom_out()
        widget.zoom_out()
        qapp.processEvents()
        assert widget._scrollbar.maximum() == 0  # now fits without scrolling

        widget._scrollbar.setValue(0)
        widget.zoom_in()
        widget.zoom_in()
        widget.zoom_in()
        qapp.processEvents()
        assert widget._scrollbar.maximum() > 0

        widget._scrollbar.setValue(widget._scrollbar.maximum())
        qapp.processEvents()
        assert widget._canvas._view.scroll_offset > 0
    finally:
        widget.shutdown()


def test_async_filmstrip_and_waveform_generation(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_async.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()
        canvas = widget._canvas

        # 3s clip at the default 50px/sec zoom renders 150px wide; a 4:3
        # tile at the 48px filmstrip height is 64px, so 3 tiles (the last
        # one cut off) -- each its own frame.
        assert pump_until(qapp, lambda: len(canvas._frames) == 3 and not canvas._pending_frames)
        assert all(not pixmap.isNull() for pixmap in canvas._frames.values())
        assert {key[0] for key in canvas._frames} == {source}
        assert pump_until(qapp, lambda: canvas._waveforms.get(source) is not None)

        # Splitting reuses the same file's waveform and (mostly) its frames.
        widget.set_playhead(Fraction(3, 2))
        widget.split_at_playhead()
        qapp.processEvents()
        assert source not in canvas._pending_waveforms
    finally:
        widget.shutdown()


def test_long_clip_requests_only_the_tiles_on_screen(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_long.mp4", duration=2.0, fps=30, size="320x240")
    # Pretend it's a 2-hour file: only the tiles in view may be requested.
    media = make_media(source, duration=7200.0)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(7200))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(640, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()
        canvas = widget._canvas
        on_screen = len(canvas._wanted_frames)
        assert 0 < on_screen <= canvas.width() // 64 + 1
        assert len(canvas._pending_frames) + len(canvas._frames) <= on_screen
    finally:
        widget.shutdown()


def test_drag_enter_with_media_mime_type_accepts(qapp) -> None:

    widget = TimelineWidget()
    try:
        mime = QMimeData()
        mime.setData(MEDIA_MIME_TYPE, b"some-media-id")
        event = QDragEnterEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget._canvas.dragEnterEvent(event)
        assert event.isAccepted()
    finally:
        widget.shutdown()


def test_drag_enter_without_media_mime_type_does_not_accept(qapp) -> None:

    widget = TimelineWidget()
    try:
        mime = QMimeData()
        mime.setText("not a media id")
        event = QDragEnterEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget._canvas.dragEnterEvent(event)
        assert not event.isAccepted()
    finally:
        widget.shutdown()


def _two_clip_project(source) -> Project:
    media = make_media(source, duration=10.0)
    clip1 = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(5))
    clip2 = Clip(media_ref=media, source_in=Fraction(5), source_out=Fraction(8))
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip1, clip2])])


def test_drop_index_for_x_on_empty_track(qapp) -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    widget = TimelineWidget()
    try:
        widget.set_project(project, QUndoStack())
        assert widget._canvas._drop_index_for_x(500) == 0
    finally:
        widget.shutdown()


def test_drop_index_for_x_between_and_past_clips(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_drop_index.mp4", duration=10.0, fps=30, size="320x240")
    project = _two_clip_project(source)
    widget = TimelineWidget()
    try:
        widget.set_project(project, QUndoStack())
        # pixels_per_second defaults to 50: clip1 spans 0-250px, clip2 spans 250-400px.
        assert widget._canvas._drop_index_for_x(50) == 0  # first half of clip1
        assert widget._canvas._drop_index_for_x(200) == 1  # second half of clip1
        assert widget._canvas._drop_index_for_x(300) == 1  # first half of clip2
        assert widget._canvas._drop_index_for_x(350) == 2  # second half of clip2
        assert widget._canvas._drop_index_for_x(1000) == 2  # past everything
    finally:
        widget.shutdown()


def test_drop_event_emits_media_dropped_with_computed_index(qapp, make_video_clip) -> None:

    source = make_video_clip("tlw_drop_event.mp4", duration=10.0, fps=30, size="320x240")
    project = _two_clip_project(source)
    widget = TimelineWidget()
    try:
        widget.set_project(project, QUndoStack())
        received = []
        widget.media_dropped.connect(lambda media_id, index: received.append((media_id, index)))

        mime = QMimeData()
        mime.setData(MEDIA_MIME_TYPE, b"the-media-id")
        event = QDropEvent(
            QPointF(350, 40),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget._canvas.dropEvent(event)

        assert received == [("the-media-id", 2)]
        assert event.isAccepted()
    finally:
        widget.shutdown()


def test_drop_event_without_media_mime_type_does_nothing(qapp) -> None:

    widget = TimelineWidget()
    try:
        received = []
        widget.media_dropped.connect(lambda media_id, index: received.append((media_id, index)))

        mime = QMimeData()
        mime.setText("unrelated")
        event = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget._canvas.dropEvent(event)

        assert received == []
    finally:
        widget.shutdown()


def test_shutdown_is_safe_to_call(qapp) -> None:
    widget = TimelineWidget()
    widget.shutdown()  # must not raise or hang


def test_scrollbar_range_follows_edits(qapp, make_video_clip) -> None:
    # Regression: the scroll range was only recalculated on zoom/resize, so
    # inserting a clip (or deleting part of one) left it stale.
    source = make_video_clip("tlw_range.mp4", duration=10.0, fps=30, size="320x240")
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(400, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()
        assert widget._scrollbar.maximum() == 0

        clip = Clip(media_ref=make_media(source), source_in=Fraction(0), source_out=Fraction(10))
        widget.insert_clip(clip)
        # 10s at 50px/s = 500px of content in a 400px-wide view.
        assert widget._scrollbar.maximum() == 500 - widget._canvas.width()

        stack.undo()
        assert widget._scrollbar.maximum() == 0
    finally:
        widget.shutdown()


def test_filmstrip_tiles_follow_video_shape() -> None:
    # 48px-tall tiles: 16:9 -> ~85px each, 4:3 -> 64px, vertical 9:16 ->
    # 27px; never thinner than the 20px minimum.
    assert _tile_width(Fraction(4, 3)) == 64
    assert round(_tile_width(Fraction(16, 9))) == 85
    assert _tile_width(Fraction(9, 16)) == 27
    assert _tile_width(Fraction(1, 10)) == 20


def test_tile_grid_is_a_power_of_two_no_longer_than_a_tile() -> None:
    assert _grid_seconds(Fraction(32)) == 32  # 64px at 2px/s
    assert _grid_seconds(Fraction(64, 82)) == Fraction(1, 2)  # 64px at 82px/s
    assert _grid_seconds(Fraction(16, 125)) == Fraction(1, 8)  # 64px at 500px/s
    assert _grid_seconds(Fraction(1, 1000)) == Fraction(1, 16)  # floor


def test_tile_times_snap_to_the_grid_within_the_clip() -> None:
    media = make_media(Path("unused.mp4"), duration=100.0)
    clip = Clip(media_ref=media, source_in=Fraction(10), source_out=Fraction(20))

    assert _tile_source_time(clip, Fraction(3), Fraction(2)) == 12  # 13 -> nearest even
    assert _tile_source_time(clip, Fraction(0), Fraction(8)) == 10  # 8 is before the clip
    # past the end: the clip's last frame
    assert _tile_source_time(clip, Fraction(10), Fraction(1)) == Fraction(20) - Fraction(1, 30)


def test_right_drag_scrolls_and_right_click_opens_menu(qapp, make_video_clip, monkeypatch) -> None:
    source = make_video_clip("tlw_pan.mp4", duration=30.0, fps=30, size="320x240")
    project = make_project(source, duration=30.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(400, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()
        canvas = widget._canvas
        menus: list[float] = []
        monkeypatch.setattr(canvas, "_show_context_menu", lambda x, _pos: menus.append(x))
        right, none = Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier
        y = TRACK_TOP + 10

        # Drag left 200px: the view moves 200px (4s at 50px/s) later.
        QTest.mousePress(canvas, right, none, QPoint(300, y))
        QTest.mouseMove(canvas, QPoint(100, y))
        QTest.mouseRelease(canvas, right, none, QPoint(100, y))
        assert canvas._view.scroll_offset == Fraction(4)
        assert menus == []

        # Drag right 50px: back 1s.
        QTest.mousePress(canvas, right, none, QPoint(100, y))
        QTest.mouseMove(canvas, QPoint(150, y))
        QTest.mouseRelease(canvas, right, none, QPoint(150, y))
        assert canvas._view.scroll_offset == Fraction(3)

        # A right-click without dragging opens the menu instead.
        QTest.mousePress(canvas, right, none, QPoint(120, y))
        QTest.mouseRelease(canvas, right, none, QPoint(120, y))
        assert menus == [120]
        assert stack.count() == 0  # right-dragging a clip never moves it
    finally:
        widget.shutdown()


def test_right_click_on_a_clip_offers_delete_clip(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_menu_delete.mp4", duration=10.0, fps=30, size="320x240")
    media = make_media(source)
    clip_a = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    clip_b = Clip(media_ref=media, source_in=Fraction(3), source_out=Fraction(6))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip_a, clip_b])])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        x_in_clip_b = float(widget._canvas._view.x_for_time(Fraction(4)))
        menu = widget._canvas._build_context_menu(x_in_clip_b)
        assert menu is not None
        labels = [action.text() for action in menu.actions() if action.text()]
        assert labels == ["Delete clip", "Delete and close gap", "Delete all empty space"]

        next(a for a in menu.actions() if a.text() == "Delete clip").trigger()

        items = project.tracks[0].items
        assert items[0] is clip_a and len(items) == 1  # a trailing gap is dropped
        stack.undo()
        assert project.tracks[0].items == [clip_a, clip_b]

        # On the first clip: the second one moves up to fill its place.
        x_in_clip_a = float(widget._canvas._view.x_for_time(Fraction(1)))
        menu = widget._canvas._build_context_menu(x_in_clip_a)
        assert menu is not None
        next(a for a in menu.actions() if a.text() == "Delete and close gap").trigger()
        assert project.tracks[0].items == [clip_b]
        stack.undo()
        assert project.tracks[0].items == [clip_a, clip_b]
    finally:
        widget.shutdown()


def _wheel(widget, x: float, delta_y: int = 0, delta_x: int = 0, modifiers=None) -> None:
    from PySide6.QtGui import QWheelEvent

    position = QPointF(x, 40)
    event = QWheelEvent(
        position,
        QPointF(widget.mapToGlobal(position.toPoint())),
        QPoint(),
        QPoint(delta_x, delta_y),
        Qt.MouseButton.NoButton,
        modifiers or Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(event)


def test_mouse_wheel_zooms_around_the_pointer_and_shift_wheel_scrolls(
    qapp, make_video_clip
) -> None:
    source = make_video_clip("tlw_wheel.mp4", duration=10.0, fps=30, size="320x240")
    project = make_project(source, duration=10.0)
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(300, 150)
        widget.show()
        widget.set_project(project, stack)
        qapp.processEvents()
        canvas = widget._canvas
        canvas_view = canvas._view
        baseline = canvas_view.pixels_per_second
        time_under_pointer = canvas_view.time_for_x(200)

        _wheel(canvas, 200, delta_y=120)  # one notch up: zoom in
        assert canvas_view.pixels_per_second > baseline
        # The spot under the pointer stays put (to the pixel).
        assert abs(canvas_view.x_for_time(time_under_pointer) - 200) <= 1
        assert widget._scrollbar.value() == round(
            canvas_view.scroll_offset * canvas_view.pixels_per_second
        )

        # A touchpad's small deltas add up to one step.
        zoomed = canvas_view.pixels_per_second
        _wheel(canvas, 200, delta_y=-60)
        assert canvas_view.pixels_per_second == zoomed
        _wheel(canvas, 200, delta_y=-60)
        assert canvas_view.pixels_per_second < zoomed

        # Zooming out never leaves the view scrolled past the end.
        for _ in range(10):
            _wheel(canvas, 200, delta_y=-120)
        assert canvas_view.scroll_offset == 0

        # Shift+wheel and sideways swipes scroll instead of zooming.
        while widget._scrollbar.maximum() == 0:
            widget.zoom_in()
        widget._scrollbar.setValue(0)
        zoom = canvas_view.pixels_per_second
        _wheel(canvas, 100, delta_y=-120, modifiers=Qt.KeyboardModifier.ShiftModifier)
        qapp.processEvents()
        assert widget._scrollbar.value() > 0
        assert canvas_view.pixels_per_second == zoom
        scrolled = widget._scrollbar.value()
        _wheel(canvas, 100, delta_x=120)
        assert widget._scrollbar.value() < scrolled
        assert stack.count() == 0
    finally:
        widget.shutdown()


def test_delete_clip_at_playhead(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_playhead_delete.mp4", duration=10.0, fps=30, size="320x240")
    media = make_media(source)
    clip_a = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(3))
    clip_b = Clip(media_ref=media, source_in=Fraction(3), source_out=Fraction(6))
    items = [clip_a, Gap(duration=Fraction(2)), clip_b]
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.set_project(project, stack)
        for position, expected in [(0, 0), (Fraction(4), None), (Fraction(6), 2), (9, None)]:
            widget.set_playhead(Fraction(position))
            assert widget.clip_index_at_playhead() == expected, position

        widget.set_playhead(Fraction(1))
        widget.delete_clip_at_playhead()
        assert isinstance(project.tracks[0].items[0], Gap)  # left as empty space
        assert project.tracks[0].items[-1] is clip_b

        stack.undo()
        widget.delete_clip_at_playhead(close_gap=True)
        remaining = project.tracks[0].items
        assert [type(item) for item in remaining] == [Gap, Clip]  # only the old gap is left
        assert remaining[0].duration == 2 and remaining[1] is clip_b
    finally:
        widget.shutdown()
