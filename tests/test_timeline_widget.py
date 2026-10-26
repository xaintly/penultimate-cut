import time
from fractions import Fraction

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
    _clip_key,
    _filmstrip_frame_count,
    _thumbnail_key,
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
        # 10s clip at 50px/s spans 0..500px; grab the right trim handle near x=498.
        QTest.mousePress(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(498, y)
        )
        QTest.mouseMove(canvas, QPoint(400, y))
        QTest.mouseRelease(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(400, y)
        )
        qapp.processEvents()

        assert stack.count() == 1
        assert project.tracks[0].items[0].source_out == Fraction(8)

        stack.undo()
        assert project.tracks[0].items[0].source_out == Fraction(10)
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


def test_async_thumbnail_and_waveform_generation(qapp, make_video_clip) -> None:
    source = make_video_clip("tlw_async.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)
    clip = project.tracks[0].items[0]
    stack = QUndoStack()

    widget = TimelineWidget()
    try:
        widget.resize(800, 150)
        widget.set_project(project, stack)
        widget.show()
        qapp.processEvents()

        # 3s clip at the default 50px/sec zoom renders 150px wide -- enough
        # for 3 tiles at the 40px-per-tile minimum.
        frame_count = _filmstrip_frame_count(150)
        assert frame_count == 3
        thumb_key = _thumbnail_key(clip, frame_count)
        wave_key = _clip_key(clip)
        assert pump_until(qapp, lambda: thumb_key in widget._canvas._thumbnails)
        assert pump_until(qapp, lambda: wave_key in widget._canvas._waveforms)
        assert len(widget._canvas._thumbnails[thumb_key]) == 3
        assert len(widget._canvas._waveforms[wave_key]) == 100
        assert thumb_key not in widget._canvas._pending
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
