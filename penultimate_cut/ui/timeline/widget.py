"""Custom-painted timeline widget: one video track + its linked audio.

`TimelineWidget` is a thin wrapper around `_TimelineCanvas` (the actual
painting/mouse-handling) plus a horizontal scrollbar, kept separate so the
canvas's own coordinate math never has to think about the scrollbar's
geometry. All the real "what's going on" state (selection, drag, zoom,
scroll) lives on the canvas; the outer widget mostly just forwards its
public API down and keeps the scrollbar's range in sync.

Filmstrip thumbnails and waveform peaks are generated on a background
thread pool -- same reasoning and cross-thread-signal pattern as
`ui/media_bin.py`. Waveforms are keyed by `(clip.id, source_in, source_out)`
so trimming a clip (same id, new range) naturally invalidates the
in-memory cache instead of needing an explicit invalidation step.
Thumbnails are keyed by that same triple plus how many tiles the clip's
current on-screen width calls for, so a long clip gets a real filmstrip
(several distinct frames tiled across its width) instead of one frame
stretched over the whole thing, and zooming in asks for more tiles instead
of stretching the same handful further. The on-disk cache in
media/thumbnails.py and media/waveform.py is unaffected either way.

Live edits during a drag (trimming, reordering) are previewed by computing
a scratch `Track` with the real `timeline_ops` function and painting that
instead of `project.tracks[track_index]` -- never touching the project or
the undo stack until the mouse is released, so a drag that goes nowhere
never produces an undo step.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QUndoStack,
)
from PySide6.QtWidgets import QMenu, QScrollBar, QSizePolicy, QVBoxLayout, QWidget

from penultimate_cut.commands.timeline_commands import (
    DeleteCommand,
    InsertCommand,
    MoveCommand,
    RemoveGapsCommand,
    SplitCommand,
    TrimCommand,
)
from penultimate_cut.media.thumbnails import ThumbnailError, filmstrip
from penultimate_cut.media.waveform import Peak, WaveformError, peaks
from penultimate_cut.model.project import Clip, Gap, Project, Track, TrackItem
from penultimate_cut.model.time import snap_to_frame
from penultimate_cut.model.timeline_ops import TimelineOpError, move_item, trim_item
from penultimate_cut.ui.media_bin import MEDIA_MIME_TYPE
from penultimate_cut.ui.timeline.geometry import TimelineView, hit_test, item_x_range

RULER_HEIGHT = 20
TRACK_TOP = RULER_HEIGHT
TRACK_HEIGHT = 80
MIN_HEIGHT = TRACK_TOP + TRACK_HEIGHT + 4

DEFAULT_PIXELS_PER_SECOND = Fraction(50)
MIN_PIXELS_PER_SECOND = Fraction(2)
MAX_PIXELS_PER_SECOND = Fraction(500)
ZOOM_FACTOR = Fraction(3, 2)

FILMSTRIP_HEIGHT = int(TRACK_HEIGHT * 0.6)
"""Filmstrip on top, waveform in the remaining 40% below it."""
MIN_FRAME_WIDTH_PX = 20
"""Each filmstrip tile is at least this many pixels wide on screen, even
for a very tall/narrow (e.g. vertical phone) video."""
DEFAULT_ASPECT = Fraction(16, 9)
"""Tile shape for media whose dimensions are unknown."""
MAX_FILMSTRIP_FRAMES = 80
"""Upper bound on tiles per clip, so a long clip zoomed in far doesn't
trigger an unreasonable number of ffmpeg frame extractions."""
WAVEFORM_CACHE_COUNT = 100

DRAG_THRESHOLD_PX = 4.0
"""How far the mouse has to move, after pressing on a clip body, before a
click turns into a reorder drag."""

_TICK_INTERVALS_S = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]
_MIN_TICK_SPACING_PX = 50.0

_RULER_BG = QColor(40, 40, 40)
_RULER_TEXT = QColor(190, 190, 190)
_TRACK_BG = QColor(26, 26, 26)
_CLIP_BG = QColor(55, 85, 120)
_CLIP_BORDER = QColor(80, 110, 150)
_SELECTED_BORDER = QColor(255, 195, 0)
_GAP_BG = QColor(45, 45, 45)
_WAVEFORM_COLOR = QColor(170, 195, 220)
_PLAYHEAD_COLOR = QColor(230, 40, 40)
_LABEL_COLOR = QColor(235, 235, 235)


WaveformKey = tuple[str, Fraction, Fraction]
ThumbnailKey = tuple[str, Fraction, Fraction, int]  # + frame_count


def _clip_key(clip: Clip) -> WaveformKey:
    return (clip.id, clip.source_in, clip.source_out)


def _thumbnail_key(clip: Clip, frame_count: int) -> ThumbnailKey:
    return (*_clip_key(clip), frame_count)


def _filmstrip_frame_count(width_px: float, aspect: Fraction = DEFAULT_ASPECT) -> int:
    """How many distinct frames to tile across a clip rendered `width_px`
    wide: as many as fit at roughly the video's own shape (`aspect` =
    width/height) at filmstrip height, so tiles are neither squeezed nor
    stretched -- except past `MAX_FILMSTRIP_FRAMES`, where they stretch."""
    tile_width = max(MIN_FRAME_WIDTH_PX, float(FILMSTRIP_HEIGHT * aspect))
    return max(1, min(MAX_FILMSTRIP_FRAMES, round(width_px / tile_width)))


def _aspect(clip: Clip) -> Fraction:
    width, height = clip.media_ref.width, clip.media_ref.height
    return Fraction(width, height) if width and height else DEFAULT_ASPECT


@dataclass
class _TrimDrag:
    kind: Literal["trim"]
    index: int
    edge: Literal["start", "end"]
    original_time: Fraction
    """The edge's source-media time when the drag began."""
    press_time: Fraction
    """Timeline time under the mouse when the drag began -- the drag moves
    the edge by however far the mouse has moved since, not *to* the mouse:
    timeline time and source-media time are different clocks (a clip
    starting 5s into its file sits at 0:00 on the timeline)."""


@dataclass
class _ReorderDrag:
    kind: Literal["reorder"]
    start_index: int
    press_x: float
    armed: bool = False
    target_index: int | None = None


DragState = _TrimDrag | _ReorderDrag


@dataclass
class _Pan:
    """A right-button press: a drag scrolls the view, a click opens the menu."""

    press_x: float
    start_px: float
    """Scroll position (pixels from the timeline's start) at the press."""
    moved: bool = False


class _AsyncSignals(QObject):
    thumbnails_ready = Signal(object, list)  # ThumbnailKey, list[Path]
    waveform_ready = Signal(object, list)  # WaveformKey, list[Peak]
    job_finished = Signal(object)  # ThumbnailKey -- always emitted once, whatever the outcome


class _TimelineCanvas(QWidget):
    playhead_moved = Signal(object)  # Fraction
    selection_changed = Signal(object)  # int | None
    content_changed = Signal()  # project/zoom/scroll changed -> scrollbar range may need updating
    media_dropped = Signal(str, int)  # media_id, insert index
    scroll_requested = Signal(int)  # pixels from the timeline's start

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        # Take focus on click, so the Delete key acts on whichever of the
        # timeline and the media bin was clicked last.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._pan: _Pan | None = None

        self._project: Project | None = None
        self._undo_stack: QUndoStack | None = None
        self._track_index = 0
        self._view = TimelineView(pixels_per_second=DEFAULT_PIXELS_PER_SECOND)
        self._playhead = Fraction(0)
        self._selected_index: int | None = None
        self._drag: DragState | None = None
        self._last_mouse_x = 0.0

        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="timeline-assets")
        self._pending: set[ThumbnailKey] = set()
        self._thumbnails: dict[ThumbnailKey, list[QPixmap]] = {}
        self._waveforms: dict[WaveformKey, list[Peak]] = {}
        self._signals = _AsyncSignals()
        self._signals.thumbnails_ready.connect(self._on_thumbnails_ready)
        self._signals.waveform_ready.connect(self._on_waveform_ready)
        self._signals.job_finished.connect(self._on_job_finished)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        # The stack can outlive this widget, and its destructor emits
        # indexChanged (via clear()) -- into a deleted canvas, if still
        # connected: a hard crash.
        if self._undo_stack is not None:
            self._undo_stack.indexChanged.disconnect(self._on_edited)
            self._undo_stack = None

    # --- project / track access -------------------------------------------------

    def set_project(self, project: Project, undo_stack: QUndoStack, track_index: int = 0) -> None:
        if self._undo_stack is not None:
            self._undo_stack.indexChanged.disconnect(self._on_edited)
        self._project = project
        self._undo_stack = undo_stack
        # Every edit (and undo/redo) goes through the stack, so this is the
        # one place to hear that the track's length may have changed.
        undo_stack.indexChanged.connect(self._on_edited)
        self._track_index = track_index
        self._selected_index = None
        self._thumbnails.clear()
        self._waveforms.clear()
        self._pending.clear()
        self.content_changed.emit()
        self.update()

    def _on_edited(self, _index: int) -> None:
        self.content_changed.emit()
        self.update()

    @property
    def track(self) -> Track | None:
        if self._project is None:
            return None
        return self._project.tracks[self._track_index]

    # --- public editing API -------------------------------------------------------

    def split_at_playhead(self) -> None:
        if self._project is None or self._undo_stack is None:
            return
        try:
            self._undo_stack.push(SplitCommand(self._project, self._track_index, self._playhead))
        except TimelineOpError:
            pass

    def delete_selected(self, *, ripple: bool) -> None:
        if self._project is None or self._undo_stack is None or self._selected_index is None:
            return
        try:
            self._undo_stack.push(
                DeleteCommand(self._project, self._track_index, self._selected_index, ripple=ripple)
            )
        except TimelineOpError:
            pass
        self._selected_index = None
        self.selection_changed.emit(None)
        self.update()

    def insert_clip(self, item: TrackItem, index: int | None = None) -> None:
        track = self.track
        if self._project is None or self._undo_stack is None or track is None:
            return
        target = len(track.items) if index is None else index
        self._undo_stack.push(InsertCommand(self._project, self._track_index, target, item))
        self.update()

    def zoom_in(self) -> None:
        self._set_zoom(self._view.pixels_per_second * ZOOM_FACTOR)

    def zoom_out(self) -> None:
        self._set_zoom(self._view.pixels_per_second / ZOOM_FACTOR)

    def _set_zoom(self, pixels_per_second: Fraction) -> None:
        clamped = max(MIN_PIXELS_PER_SECOND, min(MAX_PIXELS_PER_SECOND, pixels_per_second))
        self._view.pixels_per_second = clamped
        self.content_changed.emit()
        self.update()

    def set_playhead(self, position: Fraction) -> None:
        self._playhead = max(Fraction(0), position)
        self.update()

    def set_scroll_offset(self, offset_seconds: Fraction) -> None:
        self._view.scroll_offset = max(Fraction(0), offset_seconds)
        self.update()

    # --- painting ---------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), _TRACK_BG)
        self._paint_ruler(painter)
        track = self.track
        if track is not None:
            self._paint_track(painter, track)
        self._paint_playhead(painter)
        painter.end()

    def _paint_ruler(self, painter: QPainter) -> None:
        ruler_rect = QRect(0, 0, self.width(), RULER_HEIGHT)
        painter.fillRect(ruler_rect, _RULER_BG)
        interval = _ruler_tick_interval(self._view.pixels_per_second)
        start_tick = (self._view.scroll_offset // interval) * interval
        painter.setPen(_RULER_TEXT)
        time = start_tick
        while True:
            x = self._view.x_for_time(time)
            if x > self.width():
                break
            if x >= -1:
                painter.drawLine(int(x), RULER_HEIGHT - 5, int(x), RULER_HEIGHT)
                painter.drawText(int(x) + 3, RULER_HEIGHT - 6, _format_tick(time))
            time += interval

    def _paint_track(self, painter: QPainter, track: Track) -> None:
        preview = self._preview_track(track)
        for index, item in enumerate(preview.items):
            start_x, end_x = item_x_range(preview, self._view, index)
            if end_x < 0 or start_x > self.width():
                continue
            rect = QRect(int(start_x), TRACK_TOP, max(1, int(end_x - start_x)), TRACK_HEIGHT)
            if isinstance(item, Gap):
                self._paint_gap(painter, rect)
            else:
                self._paint_clip(painter, rect, item, selected=index == self._selected_index)

    def _preview_track(self, track: Track) -> Track:
        if isinstance(self._drag, _TrimDrag):
            try:
                return trim_item(
                    track, self._drag.index, self._drag.edge, self._live_trim_time(self._drag)
                )
            except TimelineOpError:
                return track
        if (
            isinstance(self._drag, _ReorderDrag)
            and self._drag.armed
            and self._drag.target_index is not None
        ):
            try:
                return move_item(track, self._drag.start_index, self._drag.target_index)
            except TimelineOpError:
                return track
        return track

    def _paint_gap(self, painter: QPainter, rect: QRect) -> None:
        painter.fillRect(rect, _GAP_BG)
        painter.setPen(QColor(70, 70, 70))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_clip(self, painter: QPainter, rect: QRect, clip: Clip, *, selected: bool) -> None:
        painter.fillRect(rect, _CLIP_BG)
        frame_count = _filmstrip_frame_count(rect.width(), _aspect(clip))
        self._paint_filmstrip(painter, rect, clip, frame_count)
        self._paint_waveform(painter, rect, clip, frame_count)
        painter.setPen(_SELECTED_BORDER if selected else _CLIP_BORDER)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(_LABEL_COLOR)
        label_rect = rect.adjusted(3, 1, -3, -rect.height() + 14)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            clip.media_ref.path.name,
        )

    def _paint_filmstrip(
        self, painter: QPainter, rect: QRect, clip: Clip, frame_count: int
    ) -> None:
        if not clip.media_ref.has_video:
            return
        key = _thumbnail_key(clip, frame_count)
        cached = self._thumbnails.get(key)
        self._ensure_assets(clip, frame_count)
        if not cached:
            return
        frame_width = rect.width() / len(cached)
        for slot, pixmap in enumerate(cached):
            if pixmap.isNull():
                continue
            frame_rect = QRect(
                int(rect.x() + slot * frame_width), rect.y(), int(frame_width) + 1, FILMSTRIP_HEIGHT
            )
            painter.drawPixmap(frame_rect, pixmap)

    def _paint_waveform(self, painter: QPainter, rect: QRect, clip: Clip, frame_count: int) -> None:
        if not clip.media_ref.has_audio:
            return
        key = _clip_key(clip)
        cached = self._waveforms.get(key)
        self._ensure_assets(clip, frame_count)
        if not cached:
            return
        wave_top = rect.y() + int(rect.height() * 0.6)
        wave_height = rect.height() - int(rect.height() * 0.6)
        mid_y = wave_top + wave_height // 2
        painter.setPen(_WAVEFORM_COLOR)
        n = len(cached)
        for pixel in range(rect.width()):
            sample_index = min(n - 1, int(pixel / max(1, rect.width()) * n))
            lo, hi = cached[sample_index]
            x = rect.x() + pixel
            y1 = mid_y - int(hi * (wave_height / 2))
            y2 = mid_y - int(lo * (wave_height / 2))
            painter.drawLine(x, y1, x, max(y1, y2))

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self._view.x_for_time(self._playhead)
        if x < 0 or x > self.width():
            return
        painter.setPen(_PLAYHEAD_COLOR)
        painter.drawLine(int(x), 0, int(x), self.height())

    # --- async thumbnail / waveform generation -------------------------------------

    def _ensure_assets(self, clip: Clip, frame_count: int) -> None:
        job_key = _thumbnail_key(clip, frame_count if clip.media_ref.has_video else 0)
        if job_key in self._pending:
            return
        needs_thumbnail = clip.media_ref.has_video and job_key not in self._thumbnails
        needs_waveform = clip.media_ref.has_audio and _clip_key(clip) not in self._waveforms
        if not needs_thumbnail and not needs_waveform:
            return
        self._pending.add(job_key)
        self._executor.submit(self._generate_assets, clip, frame_count, job_key)

    def _generate_assets(self, clip: Clip, frame_count: int, job_key: ThumbnailKey) -> None:
        # Runs on a worker thread. One `job_finished` at the end, always,
        # regardless of which parts succeeded -- that's the only thing that
        # clears `_pending`, so a clip with both video and audio can't have
        # its still-in-flight waveform job redundantly resubmitted the
        # moment its thumbnail part happens to land first.
        span = clip.source_out - clip.source_in
        if clip.media_ref.has_video:
            try:
                paths = filmstrip(
                    clip.media_ref.path,
                    span,
                    count=frame_count,
                    start=clip.source_in,
                    is_still_image=clip.media_ref.is_still_image,
                )
                self._signals.thumbnails_ready.emit(_thumbnail_key(clip, frame_count), paths)
            except ThumbnailError:
                pass
        if clip.media_ref.has_audio:
            try:
                peak_list = peaks(
                    clip.media_ref.path, span, count=WAVEFORM_CACHE_COUNT, start=clip.source_in
                )
                self._signals.waveform_ready.emit(_clip_key(clip), peak_list)
            except WaveformError:
                pass
        self._signals.job_finished.emit(job_key)

    # Results are stored under the exact key they were generated for, never
    # re-derived from whichever clip currently has that id: a split or trim
    # landing while a job is in flight would otherwise file the old range's
    # frames under the new range's key. A result for a range no clip has any
    # more is just never looked up.

    def _on_thumbnails_ready(self, key: ThumbnailKey, paths: list[Path]) -> None:
        self._thumbnails[key] = [QPixmap(str(p)) for p in paths]
        self.update()

    def _on_waveform_ready(self, key: WaveformKey, peak_list: list[Peak]) -> None:
        self._waveforms[key] = peak_list
        self.update()

    def _on_job_finished(self, key: ThumbnailKey) -> None:
        self._pending.discard(key)

    # --- mouse interaction ---------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        track = self.track
        if track is None:
            return
        x = event.position().x()
        self._last_mouse_x = x
        if event.button() == Qt.MouseButton.RightButton:
            start_px = float(self._view.scroll_offset * self._view.pixels_per_second)
            self._pan = _Pan(press_x=x, start_px=start_px)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if event.position().y() < RULER_HEIGHT:
            self._scrub_to_x(x)
            return

        hit = hit_test(track, self._view, x)
        if hit is None:
            self._select(None)
            self._scrub_to_x(x)
            return

        item = track.items[hit.index]
        if hit.target in ("trim_start", "trim_end") and isinstance(item, Clip):
            edge: Literal["start", "end"] = "start" if hit.target == "trim_start" else "end"
            original = item.source_in if edge == "start" else item.source_out
            self._drag = _TrimDrag(
                kind="trim",
                index=hit.index,
                edge=edge,
                original_time=original,
                press_time=self._view.time_for_x(x),
            )
            self._select(hit.index)
            return

        self._select(hit.index)
        self._scrub_to_x(x)
        self._drag = _ReorderDrag(kind="reorder", start_index=hit.index, press_x=x)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        self._last_mouse_x = x
        if self._pan is not None:
            moved = x - self._pan.press_x
            if not self._pan.moved and abs(moved) >= DRAG_THRESHOLD_PX:
                self._pan.moved = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._pan.moved:
                # Content follows the mouse: dragging right reveals earlier time.
                self.scroll_requested.emit(int(self._pan.start_px - moved))
            return
        if isinstance(self._drag, _TrimDrag):
            self.update()
            return
        if isinstance(self._drag, _ReorderDrag):
            if not self._drag.armed and abs(x - self._drag.press_x) >= DRAG_THRESHOLD_PX:
                self._drag.armed = True
            if self._drag.armed:
                self._update_reorder_target(x)
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._scrub_to_x(x)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            pan, self._pan = self._pan, None
            self.unsetCursor()
            if pan is not None and not pan.moved:
                self._show_context_menu(event.position().x(), event.globalPosition().toPoint())
            return
        track = self.track
        if track is not None and isinstance(self._drag, _TrimDrag):
            self._commit_trim(self._drag)
        elif track is not None and isinstance(self._drag, _ReorderDrag) and self._drag.armed:
            self._commit_reorder(self._drag)
        self._drag = None
        self.update()

    def _scrub_to_x(self, x: float) -> None:
        time = max(Fraction(0), self._view.time_for_x(x))
        self._playhead = time
        self.playhead_moved.emit(time)
        self.update()

    def _select(self, index: int | None) -> None:
        self._selected_index = index
        self.selection_changed.emit(index)
        self.update()

    def _live_trim_time(self, drag: _TrimDrag) -> Fraction:
        """The dragged edge's new source-media time: its original time
        shifted by the mouse's movement (in whole source frames), clamped
        to what the source media and the clip's other edge allow."""
        track = self.track
        assert track is not None and self._project is not None
        clip = track.items[drag.index]
        assert isinstance(clip, Clip)
        fps = clip.media_ref.frame_rate or self._project.frame_rate
        moved = self._view.time_for_x(self._last_mouse_x) - drag.press_time
        candidate = drag.original_time + snap_to_frame(moved * clip.speed, fps)
        one_frame = 1 / fps
        if drag.edge == "start":
            return max(Fraction(0), min(candidate, clip.source_out - one_frame))
        return max(clip.source_in + one_frame, min(candidate, clip.media_ref.duration))

    def _commit_trim(self, drag: _TrimDrag) -> None:
        if self._project is None or self._undo_stack is None:
            return
        new_time = self._live_trim_time(drag)
        if new_time == drag.original_time:
            return
        try:
            self._undo_stack.push(
                TrimCommand(self._project, self._track_index, drag.index, drag.edge, new_time)
            )
        except TimelineOpError:
            pass

    def _update_reorder_target(self, x: float) -> None:
        track = self.track
        if track is None:
            return
        drag = self._drag
        assert isinstance(drag, _ReorderDrag)
        hit = hit_test(track, self._view, x)
        drag.target_index = hit.index if hit is not None else drag.start_index
        self.update()

    def _commit_reorder(self, drag: _ReorderDrag) -> None:
        if self._project is None or self._undo_stack is None:
            return
        if drag.target_index is None or drag.target_index == drag.start_index:
            return
        try:
            self._undo_stack.push(
                MoveCommand(self._project, self._track_index, drag.start_index, drag.target_index)
            )
        except TimelineOpError:
            pass
        self._selected_index = drag.target_index
        self.selection_changed.emit(self._selected_index)

    # --- right-click menu ----------------------------------------------------------
    #
    # Shown on right-button *release*, and only if the mouse didn't move:
    # right-drag scrolls the timeline instead (see mouseMoveEvent). Qt's own
    # context-menu event is disabled (PreventContextMenu) since it can't
    # tell a click from a drag.

    def _show_context_menu(self, x: float, global_pos: QPoint) -> None:
        menu = self._build_context_menu(x)
        if menu is not None:
            menu.exec(global_pos)

    def _build_context_menu(self, x: float) -> QMenu | None:
        track = self.track
        if track is None:
            return None
        hit = hit_test(track, self._view, x)
        hit_item = track.items[hit.index] if hit is not None else None

        # Parented to the top-level window, not this widget: the embedded
        # video preview makes most of the main window's widgets native, and
        # Qt warns "... must be a top level window" when a popup's parent is
        # one of those (see ui/preview_widget.py).
        menu = QMenu(self.window())
        if hit is not None and isinstance(hit_item, Clip):
            index = hit.index
            menu.addAction("Delete clip").triggered.connect(lambda: self.delete_clip(index))
            menu.addSeparator()
        if hit is not None and isinstance(hit_item, Gap):
            index = hit.index
            delete_gap = menu.addAction("Delete empty space")
            delete_gap.triggered.connect(lambda: self.delete_gap(index))
        delete_all = menu.addAction("Delete all empty space")
        delete_all.setEnabled(any(isinstance(item, Gap) for item in track.items))
        delete_all.triggered.connect(self.delete_all_gaps)
        return menu

    def delete_clip(self, index: int) -> None:
        """Delete the clip at `index`, leaving empty space (like the Delete key)."""
        if self._project is None or self._undo_stack is None:
            return
        self._undo_stack.push(DeleteCommand(self._project, self._track_index, index, ripple=False))
        self._select(None)

    def clip_index_at_playhead(self) -> int | None:
        """Index of the clip under the playhead, or None (empty space, or
        past the end)."""
        track = self.track
        if track is None:
            return None
        start = Fraction(0)
        for index, item in enumerate(track.items):
            end = start + item.duration
            if start <= self._playhead < end:
                return index if isinstance(item, Clip) else None
            start = end
        return None

    def delete_gap(self, index: int) -> None:
        if self._project is None or self._undo_stack is None:
            return
        self._undo_stack.push(DeleteCommand(self._project, self._track_index, index, ripple=True))
        self._select(None)

    def delete_all_gaps(self) -> None:
        if self._project is None or self._undo_stack is None:
            return
        self._undo_stack.push(RemoveGapsCommand(self._project, self._track_index))
        self._select(None)

    # --- drag and drop, media bin -> timeline ---------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(MEDIA_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        data = event.mimeData().data(MEDIA_MIME_TYPE)
        if data.isEmpty():
            return
        media_id = bytes(data).decode("utf-8")
        index = self._drop_index_for_x(event.position().x())
        self.media_dropped.emit(media_id, index)
        event.acceptProposedAction()

    def _drop_index_for_x(self, x: float) -> int:
        """Which index a clip dropped at pixel `x` should land at: before
        whichever existing item's nearer half it's over, or at the end if
        it's past everything (or the track is empty)."""
        track = self.track
        if track is None or not track.items:
            return 0
        hit = hit_test(track, self._view, x)
        if hit is None:
            return len(track.items)
        start_x, end_x = item_x_range(track, self._view, hit.index)
        return hit.index if x < (start_x + end_x) / 2 else hit.index + 1

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.content_changed.emit()


def _ruler_tick_interval(pixels_per_second: Fraction) -> Fraction:
    for seconds in _TICK_INTERVALS_S:
        if seconds * float(pixels_per_second) >= _MIN_TICK_SPACING_PX:
            return Fraction(seconds)
    return Fraction(_TICK_INTERVALS_S[-1])


def _format_tick(time: Fraction) -> str:
    total_seconds = int(time)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


class TimelineWidget(QWidget):
    playhead_moved = Signal(object)  # Fraction
    selection_changed = Signal(object)  # int | None
    media_dropped = Signal(str, int)  # media_id, insert index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._canvas = _TimelineCanvas()
        self._canvas.playhead_moved.connect(self.playhead_moved.emit)
        self._canvas.selection_changed.connect(self.selection_changed.emit)
        self._canvas.content_changed.connect(self._update_scrollbar_range)
        self._canvas.media_dropped.connect(self.media_dropped.emit)
        self._canvas.scroll_requested.connect(lambda px: self._scrollbar.setValue(px))

        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        # Qt's built-in "Scroll here / Left edge / ..." menu triggers the same
        # "must be a top level window" warning as our own menus would (see
        # _show_context_menu), and right-drag on the timeline covers it.
        self._scrollbar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._scrollbar.valueChanged.connect(self._on_scrollbar_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._canvas)
        layout.addWidget(self._scrollbar)

    def shutdown(self) -> None:
        self._canvas.shutdown()

    def set_project(self, project: Project, undo_stack: QUndoStack, track_index: int = 0) -> None:
        self._canvas.set_project(project, undo_stack, track_index)

    @property
    def playhead(self) -> Fraction:
        return self._canvas._playhead

    def set_playhead(self, position: Fraction) -> None:
        """Move the visual playhead (driven by the preview during playback).
        If it's left the visible area, page the view so it's at the left
        edge again -- playback never runs off-screen."""
        self._canvas.set_playhead(position)
        x = self._canvas._view.x_for_time(position)
        if x < 0 or x >= self._canvas.width():
            self._scrollbar.setValue(int(position * self._canvas._view.pixels_per_second))

    def split_at_playhead(self) -> None:
        self._canvas.split_at_playhead()

    def delete_selected(self, *, ripple: bool) -> None:
        self._canvas.delete_selected(ripple=ripple)

    def clip_index_at_playhead(self) -> int | None:
        return self._canvas.clip_index_at_playhead()

    def delete_clip_at_playhead(self) -> None:
        index = self._canvas.clip_index_at_playhead()
        if index is not None:
            self._canvas.delete_clip(index)

    def insert_clip(self, item: TrackItem, index: int | None = None) -> None:
        self._canvas.insert_clip(item, index)

    def zoom_in(self) -> None:
        self._canvas.zoom_in()

    def zoom_out(self) -> None:
        self._canvas.zoom_out()

    def _update_scrollbar_range(self) -> None:
        track = self._canvas.track
        total_width = self._canvas._view.width_for_duration(track.duration) if track else 0.0
        visible_width = self._canvas.width()
        overflow = max(0, int(total_width - visible_width))
        self._scrollbar.setRange(0, overflow)
        self._scrollbar.setPageStep(max(1, int(visible_width)))

    def _on_scrollbar_changed(self, value: int) -> None:
        pps = self._canvas._view.pixels_per_second
        self._canvas.set_scroll_offset(Fraction(value) / pps)
