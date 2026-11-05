"""Custom-painted timeline widget: one video track + its linked audio.

`TimelineWidget` is a thin wrapper around `_TimelineCanvas` (the actual
painting/mouse-handling) plus a horizontal scrollbar, kept separate so the
canvas's own coordinate math never has to think about the scrollbar's
geometry. All the real "what's going on" state (selection, drag, zoom,
scroll) lives on the canvas; the outer widget mostly just forwards its
public API down and keeps the scrollbar's range in sync.

Filmstrip frames and waveforms are generated on background threads --
same reasoning and cross-thread-signal pattern as `ui/media_bin.py` -- and
both belong to the media file, not the clip, so splitting or trimming a clip
reuses what's already there.

Filmstrip tiles are drawn at the video's own shape, never stretched, and
only the tiles on screen are ever requested, so a 2-hour clip costs no more
than a 2-minute one. Each tile shows the frame at the nearest point of a
time grid in the source file whose spacing is a power of two seconds, at
most one tile's worth: a finer zoom level's grid contains every point of a
coarser one, so zooming mostly reuses frames already made. Tiles up to a
screen's width either side are made too, after the ones on screen, so
scrolling a little finds them ready. Frames queued for a tile that has
since scrolled out of that range are skipped, not extracted.

The waveform is computed once per media file, on its own worker so a long
file never holds up filmstrip frames, and painted only for visible pixels.

A trim is previewed live by computing a scratch `Track` with the real
`timeline_ops` function and painting that instead of
`project.tracks[track_index]`. Moving a clip, or dragging media in from the
bin, leaves the track still: a see-through ghost of the clip follows the
pointer and a marker shows where it would land (`geometry.drop_target`;
into empty space, the ghost sits exactly there). Neither touches the
project or the undo stack until the mouse is released, so a drag that goes
nowhere never produces an undo step.
"""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QUndoStack,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QScrollBar, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from penultimate_cut.commands.timeline_commands import (
    DeleteCommand,
    InsertCommand,
    MoveCommand,
    PlaceCommand,
    RemoveGapsCommand,
    SplitCommand,
    TrimCommand,
)
from penultimate_cut.media.thumbnails import ThumbnailError, frame_at
from penultimate_cut.media.waveform import (
    Waveform,
    WaveformCancelled,
    WaveformError,
    media_waveform,
    peak_between,
)
from penultimate_cut.model.project import (
    Clip,
    Gap,
    MediaRef,
    Project,
    Title,
    Track,
    TrackItem,
    auto_resolution,
    default_clip_for,
)
from penultimate_cut.model.time import fraction_from_seconds, snap_to_frame
from penultimate_cut.model.timeline_ops import TimelineOpError, lift_item, move_item, trim_item
from penultimate_cut.ui.format import format_duration
from penultimate_cut.ui.media_bin import MEDIA_MIME_TYPE
from penultimate_cut.ui.timeline.geometry import (
    DropTarget,
    TimelineView,
    drop_target,
    hit_test,
    item_x_range,
)
from penultimate_cut.ui.titles import paint_title

RULER_HEIGHT = 20
TRACK_TOP = RULER_HEIGHT
TRACK_HEIGHT = 80
MIN_HEIGHT = TRACK_TOP + TRACK_HEIGHT + 4

DEFAULT_PIXELS_PER_SECOND = Fraction(50)
MIN_PIXELS_PER_SECOND = Fraction(2)
"""How far out the timeline zooms -- or further, for a project too long to
fit on screen at this zoom, until the whole project fits."""
MAX_PIXELS_PER_SECOND = Fraction(500)
ZOOM_FACTOR = Fraction(3, 2)

FILMSTRIP_HEIGHT = int(TRACK_HEIGHT * 0.6)
"""Filmstrip on top, waveform in the remaining 40% below it."""
MIN_FRAME_WIDTH_PX = 20
"""Each filmstrip tile is at least this many pixels wide on screen, even
for a very tall/narrow (e.g. vertical phone) video."""
DEFAULT_ASPECT = Fraction(16, 9)
"""Tile shape for media whose dimensions are unknown."""
FRAME_IMAGE_HEIGHT = FILMSTRIP_HEIGHT * 2
"""Frames are extracted at twice the tile height, to stay sharp on
high-DPI screens."""
MIN_GRID_SECONDS = Fraction(1, 16)
FRAME_WORKERS = max(2, min(8, (os.cpu_count() or 2) - 1))
"""Filmstrip frames made at once: each is its own single-threaded ffmpeg,
so about one per core, leaving one for everything else."""
FRAME_CACHE_BYTES = 64 * 1024 * 1024
EMPTY_TIMELINE_HINT = "Drag videos here, or click Import"
"""Decoded filmstrip frames kept in memory (least recently drawn dropped
first); a screenful is about 2 MB. Frames stay cached on disk regardless."""

DRAG_THRESHOLD_PX = 4.0
"""How far the mouse has to move, after pressing on a clip body, before a
click turns into a reorder drag."""
EDGE_SCROLL_ZONE_PX = 40.0
"""While moving or trimming a clip, holding the mouse this close to either
edge of the timeline (or past it) scrolls the view, so a clip can be
dragged past whatever is off-screen."""
EDGE_SCROLL_INTERVAL_MS = 30
EDGE_SCROLL_MAX_STEP_PX = 60
"""The farther into (or past) the edge zone, the faster the scroll, up to
this many pixels per tick (about 2000 pixels a second)."""

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
_WAVEFORM_MUTED_COLOR = QColor(95, 110, 125)
_FADE_SHADE = QColor(0, 0, 0, 160)
_HINT_COLOR = QColor(150, 150, 150)
_PLAYHEAD_COLOR = QColor(230, 40, 40)
_LABEL_COLOR = QColor(235, 235, 235)
_LIFTED_SHADE = QColor(26, 26, 26, 170)
"""Over a clip's own spot while it's being moved."""
_DROP_MARKER = QColor(255, 255, 255)
GHOST_OPACITY = 0.6


FrameKey = tuple[Path, Fraction | None]
"""(media file, source time); None for a still image's one frame."""


def _tile_width(aspect: Fraction = DEFAULT_ASPECT) -> float:
    """Filmstrip tile width at the video's own shape (`aspect` =
    width/height) and filmstrip height, never thinner than
    `MIN_FRAME_WIDTH_PX`."""
    return max(float(MIN_FRAME_WIDTH_PX), float(FILMSTRIP_HEIGHT * aspect))


def _grid_seconds(tile_seconds: Fraction) -> Fraction:
    """The largest power of two seconds no longer than `tile_seconds` (but
    at least `MIN_GRID_SECONDS`): fine enough that neighbouring tiles never
    land on the same grid point."""
    grid = Fraction(1)
    while grid > tile_seconds and grid > MIN_GRID_SECONDS:
        grid /= 2
    while grid * 2 <= tile_seconds:
        grid *= 2
    return grid


def _tile_source_time(clip: Clip, timeline_offset: Fraction, grid: Fraction) -> Fraction:
    """The grid point nearest the source time `timeline_offset` seconds into
    `clip`, kept within the clip (its last frame at most)."""
    source = clip.source_in + timeline_offset * clip.speed
    snapped = round(source / grid) * grid
    frame = 1 / (clip.media_ref.frame_rate or Fraction(30))
    last = max(clip.source_in, clip.source_out - frame)
    return min(max(snapped, clip.source_in), last)


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
    grab_px: float
    """How far into the clip it was grabbed: its ghost keeps that spot
    under the pointer."""
    armed: bool = False
    cancelled: bool = False
    """Esc was pressed: the rest of this press does nothing."""
    lifted: Track | None = None
    """The track with the clip lifted out (`timeline_ops.lift_item`), what
    a drop lands in; set once the drag is armed."""
    target: DropTarget | None = None


@dataclass
class _BinDrop:
    """Media from the media bin being dragged over the timeline."""

    media_id: str
    clip: Clip
    """The clip a drop would add, for its ghost."""
    target: DropTarget | None = None


DragState = _TrimDrag | _ReorderDrag


@dataclass
class _Pan:
    """A right-button press: a drag scrolls the view, a click opens the menu."""

    press_x: float
    start_px: float
    """Scroll position (pixels from the timeline's start) at the press."""
    moved: bool = False


class _AsyncSignals(QObject):
    # Exactly one of these per request, whatever the outcome -- that's what
    # clears it from the pending set.
    frame_ready = Signal(object, object, bool)  # FrameKey, Path | None (failed), skipped
    waveform_ready = Signal(object, object)  # media Path, Waveform | None (failed)


class _TimelineCanvas(QWidget):
    playhead_moved = Signal(object)  # Fraction
    selection_changed = Signal(object)  # int | None
    content_changed = Signal()  # project/zoom/scroll changed -> scrollbar range may need updating
    media_dropped = Signal(str, int, object)  # media_id, index, offset (see place_item)
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
        self._bin_drop: _BinDrop | None = None
        self._last_mouse_x = 0.0
        self._wheel_zoom = 0
        """Wheel movement not yet turned into a zoom step (120 = one notch)."""
        self._edge_scroll_timer = QTimer(self)
        self._edge_scroll_timer.setInterval(EDGE_SCROLL_INTERVAL_MS)
        self._edge_scroll_timer.timeout.connect(self._on_edge_scroll)

        self._frame_executor = ThreadPoolExecutor(
            max_workers=FRAME_WORKERS, thread_name_prefix="filmstrip"
        )
        self._waveform_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waveform")
        self._closing = False
        self._frames: OrderedDict[FrameKey, QPixmap] = OrderedDict()
        """Least recently drawn first; a null pixmap marks a failed frame."""
        self._frame_bytes = 0
        self._pending_frames: set[FrameKey] = set()
        self._frame_requests: dict[FrameKey, MediaRef] = {}
        """Collected while painting, submitted once the paint has finished
        building `_wanted_frames`."""
        self._frame_lock = threading.Lock()
        self._queued_frames: dict[FrameKey, MediaRef] = {}
        """Submitted but not yet started, oldest first; guarded by
        `_frame_lock`. Each worker job takes the most useful one."""
        self._wanted_frames: frozenset[FrameKey] = frozenset()
        """The tiles on screen or near it at the last paint. Read by worker
        threads, only ever replaced whole."""
        self._visible_frames: frozenset[FrameKey] = frozenset()
        """Just the ones on screen, made first. Same rules."""
        self._waveforms: dict[Path, Waveform | None] = {}
        self._pending_waveforms: set[Path] = set()
        self._title_tiles: dict[tuple[Title, int, int], QPixmap] = {}
        """Small pictures of title cards, drawn right here (no worker needed)."""
        self._signals = _AsyncSignals()
        self._signals.frame_ready.connect(self._on_frame_ready)
        self._signals.waveform_ready.connect(self._on_waveform_ready)

    def shutdown(self) -> None:
        self._closing = True  # also stops an in-progress waveform's ffmpeg
        self._edge_scroll_timer.stop()
        self._frame_executor.shutdown(wait=False, cancel_futures=True)
        self._waveform_executor.shutdown(wait=False, cancel_futures=True)
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

    @property
    def scroll_duration(self) -> Fraction:
        """How much timeline there is to scroll through. During a trim drag
        that lengthens the track, the longer of the two, so the new end can
        be scrolled to -- but never shorter mid-drag: shrinking the scroll
        range would shift the view under the mouse, moving the edge being
        dragged, which shrinks the range again."""
        track = self.track
        if track is None:
            return Fraction(0)
        return max(track.duration, self._preview_track(track).duration)

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

    def insert_clip(self, item: TrackItem, index: int | None = None, text: str = "Insert") -> None:
        track = self.track
        if self._project is None or self._undo_stack is None or track is None:
            return
        target = len(track.items) if index is None else index
        self._undo_stack.push(InsertCommand(self._project, self._track_index, target, item, text))
        self.update()

    def place_clip(self, item: TrackItem, index: int, offset: Fraction) -> None:
        """Add `item` where something dropped on the timeline goes (see
        `timeline_ops.place_item`)."""
        if self._project is None or self._undo_stack is None:
            return
        try:
            self._undo_stack.push(
                PlaceCommand(self._project, self._track_index, index, offset, item)
            )
        except TimelineOpError:
            pass

    def select(self, index: int | None) -> None:
        self._select(index)

    def index_at_playhead(self) -> int:
        """Where something inserted "at the playhead" goes: before the item
        under the playhead, or at the end once the playhead is past them all."""
        track = self.track
        if track is None:
            return 0
        start = Fraction(0)
        for index, item in enumerate(track.items):
            if self._playhead < start + item.duration:
                return index
            start += item.duration
        return len(track.items)

    def zoom_in(self, anchor_x: float | None = None) -> None:
        self._set_zoom(self._view.pixels_per_second * ZOOM_FACTOR, anchor_x)

    def zoom_out(self, anchor_x: float | None = None) -> None:
        self._set_zoom(self._view.pixels_per_second / ZOOM_FACTOR, anchor_x)

    def _set_zoom(self, pixels_per_second: Fraction, anchor_x: float | None) -> None:
        """Zoom keeping the time at pixel `anchor_x` where it is on screen:
        the mouse pointer for the wheel; for the keys, the playhead if it's
        in view, otherwise the middle."""
        clamped = max(self._min_pixels_per_second(), min(MAX_PIXELS_PER_SECOND, pixels_per_second))
        if anchor_x is None:
            playhead_x = self._view.x_for_time(self._playhead)
            anchor_x = playhead_x if 0 <= playhead_x <= self.width() else self.width() / 2
        anchor_time = self._view.time_for_x(anchor_x)
        self._view.pixels_per_second = clamped
        offset = anchor_time - fraction_from_seconds(anchor_x / float(clamped))
        track = self.track
        visible = fraction_from_seconds(self.width() / float(clamped))
        latest = max(Fraction(0), (track.duration if track else Fraction(0)) - visible)
        self._view.scroll_offset = max(Fraction(0), min(offset, latest))
        self.content_changed.emit()  # scrollbar range first, then its position
        self.scroll_requested.emit(round(self._view.scroll_offset * clamped))
        self.update()

    def _min_pixels_per_second(self) -> Fraction:
        track = self.track
        if track is None or track.duration <= 0 or self.width() <= 0:
            return MIN_PIXELS_PER_SECOND
        return min(MIN_PIXELS_PER_SECOND, Fraction(self.width()) / track.duration)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """The wheel zooms around the pointer; Shift+wheel and sideways
        touchpad swipes scroll. A one-track timeline has nothing to scroll
        vertically, so the plain wheel is free for the more common need."""
        delta = event.angleDelta()
        sideways = abs(delta.x()) > abs(delta.y())
        if sideways or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            amount = delta.x() if sideways else delta.y()
            left_px = round(self._view.scroll_offset * self._view.pixels_per_second)
            self.scroll_requested.emit(left_px - round(amount / 120 * self.width() / 8))
        else:
            # Touchpads send many small deltas; zoom a step per notch's worth.
            self._wheel_zoom += delta.y()
            while abs(self._wheel_zoom) >= 120:
                step = 120 if self._wheel_zoom > 0 else -120
                self._wheel_zoom -= step
                if step > 0:
                    self.zoom_in(event.position().x())
                else:
                    self.zoom_out(event.position().x())
        event.accept()

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
        wanted: set[FrameKey] = set()
        visible: set[FrameKey] = set()
        if track is not None:
            self._paint_track(painter, track, wanted, visible)
        if track is None or not track.items:
            painter.setPen(_HINT_COLOR)
            painter.drawText(
                QRect(0, TRACK_TOP, self.width(), TRACK_HEIGHT),
                Qt.AlignmentFlag.AlignCenter,
                EMPTY_TIMELINE_HINT,
            )
        self._paint_drop(painter, wanted, visible)
        self._paint_playhead(painter)
        painter.end()
        self._visible_frames = frozenset(visible)
        self._wanted_frames = frozenset(wanted)
        self._submit_frame_requests()

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

    def _paint_track(
        self, painter: QPainter, track: Track, wanted: set[FrameKey], visible: set[FrameKey]
    ) -> None:
        preview = self._preview_track(track)
        margin = self.width()  # filmstrip tiles are made this far off screen too
        for index, item in enumerate(preview.items):
            start_x, end_x = item_x_range(preview, self._view, index)
            if end_x < -margin or start_x > self.width() + margin:
                continue
            rect = QRect(int(start_x), TRACK_TOP, max(1, int(end_x - start_x)), TRACK_HEIGHT)
            if end_x < 0 or start_x > self.width():
                if isinstance(item, Clip):
                    self._want_filmstrip(rect, item, wanted, visible)
            elif isinstance(item, Gap):
                self._paint_gap(painter, rect)
            else:
                self._paint_clip(
                    painter,
                    rect,
                    item,
                    selected=index == self._selected_index,
                    wanted=wanted,
                    visible=visible,
                )

    def _preview_track(self, track: Track) -> Track:
        if isinstance(self._drag, _TrimDrag):
            try:
                return trim_item(
                    track, self._drag.index, self._drag.edge, self._live_trim_time(self._drag)
                )
            except TimelineOpError:
                return track
        return track

    def _paint_drop(self, painter: QPainter, wanted: set[FrameKey], visible: set[FrameKey]) -> None:
        """While a clip is being moved (or media dragged in from the bin): its
        own spot shaded, a see-through ghost of it, and a marker where it
        would go."""
        drag, drop, track = self._drag, self._bin_drop, self.track
        if track is None:
            return
        if isinstance(drag, _ReorderDrag) and drag.armed and drag.target is not None:
            clip = track.items[drag.start_index]
            start_x, end_x = item_x_range(track, self._view, drag.start_index)
            painter.fillRect(
                QRectF(start_x, TRACK_TOP, end_x - start_x, TRACK_HEIGHT), _LIFTED_SHADE
            )
            target, ghost_x = drag.target, self._last_mouse_x - drag.grab_px
        elif drop is not None and drop.target is not None:
            clip, target, ghost_x = drop.clip, drop.target, self._last_mouse_x
        else:
            return
        if not isinstance(clip, Clip):
            return
        if target.into_space:
            ghost_x = self._view.x_for_time(target.time)
        width = self._view.width_for_duration(clip.duration)
        rect = QRect(int(ghost_x), TRACK_TOP, max(1, int(width)), TRACK_HEIGHT)
        painter.save()
        painter.setOpacity(GHOST_OPACITY)
        self._paint_clip(painter, rect, clip, selected=True, wanted=wanted, visible=visible)
        painter.restore()
        marker_x = self._view.x_for_time(target.time)
        painter.fillRect(QRectF(marker_x - 1.5, TRACK_TOP - 4, 3, TRACK_HEIGHT + 8), _DROP_MARKER)

    def _paint_gap(self, painter: QPainter, rect: QRect) -> None:
        painter.fillRect(rect, _GAP_BG)
        painter.setPen(QColor(70, 70, 70))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_clip(
        self,
        painter: QPainter,
        rect: QRect,
        clip: Clip,
        *,
        selected: bool,
        wanted: set[FrameKey],
        visible: set[FrameKey],
    ) -> None:
        painter.fillRect(rect, _CLIP_BG)
        self._paint_filmstrip(painter, rect, clip, wanted, visible)
        self._paint_waveform(painter, rect, clip)
        self._paint_fades(painter, rect, clip)
        painter.setPen(_SELECTED_BORDER if selected else _CLIP_BORDER)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(_LABEL_COLOR)
        label_rect = rect.adjusted(3, 1, -3, -rect.height() + 14)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            clip.media_ref.display_name + (" · muted" if clip.muted else ""),
        )

    def _paint_fades(self, painter: QPainter, rect: QRect, clip: Clip) -> None:
        """A dark wedge over each faded end, darkest where the picture is black."""
        width = float(self._view.width_for_duration(clip.fade_length))
        top, bottom = float(rect.top()), float(rect.bottom() + 1)
        left, right = float(rect.left()), float(rect.right() + 1)
        wedges = []
        if clip.fade_in:
            wedges.append([QPointF(left, top), QPointF(left + width, top), QPointF(left, bottom)])
        if clip.fade_out:
            wedges.append(
                [QPointF(right, top), QPointF(right - width, top), QPointF(right, bottom)]
            )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_FADE_SHADE)
        for wedge in wedges:
            painter.drawPolygon(QPolygonF(wedge))
        painter.restore()

    def _filmstrip_tiles(self, rect: QRect, clip: Clip) -> Iterator[tuple[float, FrameKey, bool]]:
        """(x, frame, on screen) for each of `clip`'s filmstrip tiles (the
        clip drawn at `rect`) on screen or within a screen's width of it."""
        media = clip.media_ref
        if not media.has_video or media.title is not None:
            return
        tile_width = _tile_width(_aspect(clip))
        tile_seconds = fraction_from_seconds(tile_width) / self._view.pixels_per_second
        grid = _grid_seconds(tile_seconds * clip.speed)
        tile_count = math.ceil(rect.width() / tile_width)
        margin = self.width()
        first = max(0, int((-margin - rect.x()) // tile_width))
        last = min(tile_count, int((self.width() + margin - rect.x()) // tile_width) + 1)
        for tile in range(first, last):
            time = None
            if not media.is_still_image:
                time = _tile_source_time(clip, (tile + Fraction(1, 2)) * tile_seconds, grid)
            x = rect.x() + tile * tile_width
            yield x, (media.path, time), x + tile_width > 0 and x < self.width()

    def _want_frame(
        self,
        key: FrameKey,
        media: MediaRef,
        on_screen: bool,
        wanted: set[FrameKey],
        visible: set[FrameKey],
    ) -> QPixmap | None:
        """The frame if it's made (null if it failed); otherwise asks for it."""
        wanted.add(key)
        if on_screen:
            visible.add(key)
        pixmap = self._frames.get(key)
        if pixmap is None:
            self._frame_requests[key] = media
        else:
            self._frames.move_to_end(key)
        return pixmap

    def _want_filmstrip(
        self, rect: QRect, clip: Clip, wanted: set[FrameKey], visible: set[FrameKey]
    ) -> None:
        """Ask for the frames of a clip just off screen, without drawing it."""
        for _x, key, on_screen in self._filmstrip_tiles(rect, clip):
            self._want_frame(key, clip.media_ref, on_screen, wanted, visible)

    def _paint_filmstrip(
        self,
        painter: QPainter,
        rect: QRect,
        clip: Clip,
        wanted: set[FrameKey],
        visible: set[FrameKey],
    ) -> None:
        media = clip.media_ref
        if not media.has_video:
            return
        if media.title is not None:
            self._paint_title_tiles(painter, rect, media.title)
            return
        tile_width = _tile_width(_aspect(clip))
        painter.save()
        painter.setClipRect(rect)  # the last tile is cut off, not squeezed
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for x, key, on_screen in self._filmstrip_tiles(rect, clip):
            pixmap = self._want_frame(key, media, on_screen, wanted, visible)
            if on_screen and pixmap is not None and not pixmap.isNull():
                target = QRectF(x, rect.y(), tile_width, FILMSTRIP_HEIGHT)
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        painter.restore()

    def _paint_title_tiles(self, painter: QPainter, rect: QRect, title: Title) -> None:
        """The title card itself, at the video's shape, repeated like a
        filmstrip."""
        width, height = auto_resolution(self._project) if self._project else (16, 9)
        tile_width = _tile_width(Fraction(width, height))
        pixmap = self._title_tile(title, round(tile_width * 2), FRAME_IMAGE_HEIGHT)
        first = max(0, int(-rect.x() // tile_width))
        last = min(
            math.ceil(rect.width() / tile_width), int((self.width() - rect.x()) // tile_width) + 1
        )
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for tile in range(first, last):
            target = QRectF(rect.x() + tile * tile_width, rect.y(), tile_width, FILMSTRIP_HEIGHT)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        painter.restore()

    def _title_tile(self, title: Title, width: int, height: int) -> QPixmap:
        key = (title, width, height)
        pixmap = self._title_tiles.get(key)
        if pixmap is None:
            if len(self._title_tiles) > 100:
                self._title_tiles.clear()  # only edited-away titles pile up
            pixmap = QPixmap(width, height)
            tile_painter = QPainter(pixmap)
            paint_title(tile_painter, QRectF(0, 0, width, height), title)
            tile_painter.end()
            self._title_tiles[key] = pixmap
        return pixmap

    def _paint_waveform(self, painter: QPainter, rect: QRect, clip: Clip) -> None:
        media = clip.media_ref
        if not media.has_audio:
            return
        if media.path not in self._waveforms:
            self._request_waveform(media.path)
            return
        waveform = self._waveforms[media.path]
        if waveform is None:
            return
        wave_top = rect.y() + int(rect.height() * 0.6)
        wave_height = rect.height() - int(rect.height() * 0.6)
        mid_y = wave_top + wave_height // 2
        # Drawn as loud as it'll play: taller or shorter with the clip's
        # volume (capped at the strip), and dimmed but full size when muted.
        half = wave_height / 2
        scale = half * (1.0 if clip.muted else float(clip.gain))
        painter.setPen(_WAVEFORM_MUTED_COLOR if clip.muted else _WAVEFORM_COLOR)
        # Floats, not Fractions: this runs per visible pixel on every repaint.
        source_per_pixel = float(clip.speed / self._view.pixels_per_second)
        source_in = float(clip.source_in)
        for x in range(max(rect.left(), 0), min(rect.right(), self.width() - 1) + 1):
            start = source_in + (x - rect.x()) * source_per_pixel
            lo, hi = peak_between(waveform, start, start + source_per_pixel)
            y1 = mid_y - round(max(-half, min(half, hi * scale)))
            y2 = mid_y - round(max(-half, min(half, lo * scale)))
            painter.drawLine(x, y1, x, max(y1, y2))

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self._view.x_for_time(self._playhead)
        if x < 0 or x > self.width():
            return
        painter.setPen(_PLAYHEAD_COLOR)
        painter.drawLine(int(x), 0, int(x), self.height())

    # --- async filmstrip / waveform generation -------------------------------------

    def _submit_frame_requests(self) -> None:
        requests, self._frame_requests = self._frame_requests, {}
        if self._closing:
            return
        for key, media in requests.items():
            if key not in self._pending_frames:
                self._pending_frames.add(key)
                with self._frame_lock:
                    self._queued_frames[key] = media
                self._frame_executor.submit(self._load_next_frame)

    def _load_next_frame(self) -> None:
        # Runs on a worker thread. One job per queued frame, but not
        # necessarily that one: whatever's on screen goes first, then what's
        # near it, then (skipped) whatever has scrolled away.
        with self._frame_lock:
            visible, wanted = self._visible_frames, self._wanted_frames
            queued = self._queued_frames
            key = next((k for k in queued if k in visible), None)
            if key is None:
                key = next((k for k in queued if k in wanted), next(iter(queued)))
            media = queued.pop(key)
        self._load_frame(key, media)

    def _load_frame(self, key: FrameKey, media: MediaRef) -> None:
        # Runs on a worker thread.
        if key not in self._wanted_frames:  # scrolled away while queued
            self._signals.frame_ready.emit(key, None, True)
            return
        path: Path | None
        try:
            path = frame_at(
                media.path,
                key[1] or Fraction(0),
                height=FRAME_IMAGE_HEIGHT,
                is_still_image=media.is_still_image,
            )
        except (ThumbnailError, OSError):
            path = None
        self._signals.frame_ready.emit(key, path, False)

    def _request_waveform(self, media_path: Path) -> None:
        if media_path in self._pending_waveforms or self._closing:
            return
        self._pending_waveforms.add(media_path)
        self._waveform_executor.submit(self._load_waveform, media_path)

    def _load_waveform(self, media_path: Path) -> None:
        # Runs on a worker thread.
        waveform: Waveform | None
        try:
            waveform = media_waveform(media_path, cancel_check=lambda: self._closing)
        except WaveformCancelled:
            return  # shutting down
        except (WaveformError, OSError):
            waveform = None
        self._signals.waveform_ready.emit(media_path, waveform)

    def _on_frame_ready(self, key: FrameKey, path: Path | None, skipped: bool) -> None:
        self._pending_frames.discard(key)
        if skipped:
            return  # requested again if it comes back into view
        # A null pixmap for a failed frame, so it isn't retried every repaint.
        self._store_frame(key, QPixmap(str(path)) if path is not None else QPixmap())
        self.update()

    def _store_frame(self, key: FrameKey, pixmap: QPixmap) -> None:
        self._frames[key] = pixmap
        self._frame_bytes += _pixmap_bytes(pixmap)
        while self._frame_bytes > FRAME_CACHE_BYTES and len(self._frames) > 1:
            _, evicted = self._frames.popitem(last=False)
            self._frame_bytes -= _pixmap_bytes(evicted)

    def _on_waveform_ready(self, media_path: Path, waveform: Waveform | None) -> None:
        self._pending_waveforms.discard(media_path)
        self._waveforms[media_path] = waveform
        self.update()

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
        if isinstance(item, Clip):  # empty space isn't moved, just deleted
            start_x, _ = item_x_range(track, self._view, hit.index)
            self._drag = _ReorderDrag(
                kind="reorder", start_index=hit.index, press_x=x, grab_px=x - start_x
            )

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
            self.content_changed.emit()
            self.update()
            self._update_edge_scroll()
            return
        if isinstance(self._drag, _ReorderDrag):
            drag, track = self._drag, self.track
            if drag.cancelled or track is None:
                return
            if not drag.armed and abs(x - drag.press_x) >= DRAG_THRESHOLD_PX:
                drag.armed = True
                drag.lifted, _ = lift_item(track, drag.start_index)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if drag.armed:
                self._update_reorder_target(x)
                self._update_edge_scroll()
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
        if isinstance(self._drag, _ReorderDrag):
            self.unsetCursor()
        self._drag = None
        self._edge_scroll_timer.stop()
        self.content_changed.emit()  # a lengthening trim may not have happened after all
        self.update()

    def _update_edge_scroll(self) -> None:
        if edge_scroll_step(self._last_mouse_x, self.width()):
            if not self._edge_scroll_timer.isActive():
                self._edge_scroll_timer.start()
        else:
            self._edge_scroll_timer.stop()

    def _on_edge_scroll(self) -> None:
        """A timer tick while a drag is held near an edge: scroll a step and
        let the drag follow, as though the mouse had moved over the content."""
        drag = self._drag
        step = edge_scroll_step(self._last_mouse_x, self.width())
        dragging = (
            isinstance(drag, _TrimDrag)
            or (isinstance(drag, _ReorderDrag) and drag.armed)
            or self._bin_drop is not None
        )
        if not dragging or not step:
            self._edge_scroll_timer.stop()
            return
        left_px = round(self._view.scroll_offset * self._view.pixels_per_second)
        self.scroll_requested.emit(left_px + step)
        if isinstance(drag, _ReorderDrag):
            self._update_reorder_target(self._last_mouse_x)
        elif self._bin_drop is not None:
            self._update_bin_drop_target(self._last_mouse_x)
        else:
            self.content_changed.emit()
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
        if drag.lifted is None or self._project is None:
            return
        # Past the edge of the view, the clip goes where the view's edge is
        # (the view scrolls on to bring more into reach).
        x = max(0.0, min(x, self.width() - 1.0))
        duration = track.items[drag.start_index].duration
        drag.target = drop_target(
            drag.lifted,
            self._view,
            x,
            duration,
            ghost_x=x - drag.grab_px,
            frame_rate=self._project.frame_rate,
        )
        self.update()

    def _commit_reorder(self, drag: _ReorderDrag) -> None:
        track = self.track
        if self._project is None or self._undo_stack is None or track is None:
            return
        if drag.target is None:
            return
        clip = track.items[drag.start_index]
        index, offset = drag.target.index, drag.target.offset
        try:
            if move_item(track, drag.start_index, index, offset) is track:
                return  # dropped back where it was
            self._undo_stack.push(
                MoveCommand(self._project, self._track_index, drag.start_index, index, offset)
            )
        except TimelineOpError:
            return
        moved = self.track
        assert moved is not None
        self._selected_index = next(i for i, item in enumerate(moved.items) if item is clip)
        self.selection_changed.emit(self._selected_index)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc while moving a clip: never mind, it stays where it was."""
        drag = self._drag
        if event.key() == Qt.Key.Key_Escape and isinstance(drag, _ReorderDrag) and drag.armed:
            drag.armed, drag.cancelled, drag.target = False, True, None
            self._edge_scroll_timer.stop()
            self.unsetCursor()
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

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
            close_gap = menu.addAction("Delete and close gap")
            close_gap.triggered.connect(lambda: self.delete_clip(index, close_gap=True))
            menu.addSeparator()
        if hit is not None and isinstance(hit_item, Gap):
            index = hit.index
            delete_gap = menu.addAction("Delete empty space")
            delete_gap.triggered.connect(lambda: self.delete_gap(index))
        delete_all = menu.addAction("Delete all empty space")
        delete_all.setEnabled(any(isinstance(item, Gap) for item in track.items))
        delete_all.triggered.connect(self.delete_all_gaps)
        return menu

    def delete_clip(self, index: int, *, close_gap: bool = False) -> None:
        """Delete the clip at `index`, leaving empty space (like the Delete
        key), or with `close_gap`, moving later clips up (Shift+Delete)."""
        if self._project is None or self._undo_stack is None:
            return
        self._undo_stack.push(
            DeleteCommand(self._project, self._track_index, index, ripple=close_gap)
        )
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

    # --- hover ------------------------------------------------------------------------

    def event(self, event: QEvent) -> bool:
        """Hovering over a clip shows where it came from: its name is drawn
        on the clip, but can be scrolled off-screen or cut short."""
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            track = self.track
            hit = hit_test(track, self._view, event.pos().x()) if track else None
            if track is not None and hit is not None and self._drag is None:
                QToolTip.showText(event.globalPos(), item_tooltip(track.items[hit.index]), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    # --- drag and drop, media bin -> timeline ---------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._begin_bin_drop(event):
            self._on_bin_drag_moved(event.position().x())
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._bin_drop is None:
            event.ignore()
            return
        self._on_bin_drag_moved(event.position().x())
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._end_bin_drop()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._bin_drop is None and self._begin_bin_drop(event):
            self._update_bin_drop_target(event.position().x())
        drop = self._bin_drop
        self._end_bin_drop()
        if drop is None or drop.target is None:
            return
        self.media_dropped.emit(drop.media_id, drop.target.index, drop.target.offset)
        event.acceptProposedAction()

    def _begin_bin_drop(self, event: QDropEvent) -> bool:
        """Start showing where the media being dragged in would go, if it's
        media from this project's bin."""
        data = event.mimeData().data(MEDIA_MIME_TYPE)
        if data.isEmpty() or self._project is None:
            return False
        media_id = bytes(data).decode("utf-8")
        media = next((m for m in self._project.media if m.id == media_id), None)
        if media is None:
            return False
        self._bin_drop = _BinDrop(media_id=media_id, clip=default_clip_for(media))
        return True

    def _on_bin_drag_moved(self, x: float) -> None:
        self._last_mouse_x = x
        self._update_bin_drop_target(x)
        self._update_edge_scroll()

    def _update_bin_drop_target(self, x: float) -> None:
        drop, track = self._bin_drop, self.track
        if drop is None or track is None or self._project is None:
            return
        x = max(0.0, min(x, self.width() - 1.0))
        drop.target = drop_target(
            track, self._view, x, drop.clip.duration, frame_rate=self._project.frame_rate
        )
        self.update()

    def _end_bin_drop(self) -> None:
        self._bin_drop = None
        self._edge_scroll_timer.stop()
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.content_changed.emit()


def item_tooltip(item: TrackItem) -> str:
    if isinstance(item, Gap):
        return (
            f"Empty space ({_length(item.duration)}): plays as black and silence.\n"
            "Right-click to delete it."
        )
    lines = [item.media_ref.display_name]
    if not item.media_ref.is_still_image:
        start, end = format_duration(item.source_in), format_duration(item.source_out)
        lines.append(f"Uses {start}–{end} of the original")
    lines.append(f"Length {_length(item.duration)}")
    return "\n".join(lines)


def _length(duration: Fraction) -> str:
    """Seconds with a decimal for short spans, where 00:02 would hide 2.5 s."""
    if duration < 60:
        return f"{float(duration):.1f} s"
    return format_duration(duration)


def _pixmap_bytes(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * pixmap.depth() // 8


def _ruler_tick_interval(pixels_per_second: Fraction) -> Fraction:
    for seconds in _TICK_INTERVALS_S:
        if seconds * float(pixels_per_second) >= _MIN_TICK_SPACING_PX:
            return Fraction(seconds)
    return Fraction(_TICK_INTERVALS_S[-1])


def _format_tick(time: Fraction) -> str:
    hours, rest = divmod(int(time), 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def edge_scroll_step(x: float, width: int) -> int:
    """Pixels to scroll per tick for a drag held at `x` in a view `width`
    pixels wide: negative toward the start, positive toward the end, 0 away
    from the edges."""
    if x < EDGE_SCROLL_ZONE_PX:
        depth = EDGE_SCROLL_ZONE_PX - x
        direction = -1
    elif x > width - EDGE_SCROLL_ZONE_PX:
        depth = x - (width - EDGE_SCROLL_ZONE_PX)
        direction = 1
    else:
        return 0
    return direction * max(2, min(EDGE_SCROLL_MAX_STEP_PX, round(depth / 2)))


class TimelineWidget(QWidget):
    playhead_moved = Signal(object)  # Fraction
    selection_changed = Signal(object)  # int | None
    media_dropped = Signal(str, int, object)  # media_id, index, offset (see place_item)

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

    def delete_clip_at_playhead(self, *, close_gap: bool = False) -> None:
        index = self._canvas.clip_index_at_playhead()
        if index is not None:
            self._canvas.delete_clip(index, close_gap=close_gap)

    def insert_clip(self, item: TrackItem, index: int | None = None, text: str = "Insert") -> None:
        self._canvas.insert_clip(item, index, text)

    def place_clip(self, item: TrackItem, index: int, offset: Fraction) -> None:
        self._canvas.place_clip(item, index, offset)

    def select(self, index: int | None) -> None:
        self._canvas.select(index)

    def index_at_playhead(self) -> int:
        return self._canvas.index_at_playhead()

    def zoom_in(self) -> None:
        self._canvas.zoom_in()

    def zoom_out(self) -> None:
        self._canvas.zoom_out()

    def _update_scrollbar_range(self) -> None:
        total_width = self._canvas._view.width_for_duration(self._canvas.scroll_duration)
        visible_width = self._canvas.width()
        overflow = max(0, int(total_width - visible_width))
        self._scrollbar.setRange(0, overflow)
        self._scrollbar.setPageStep(max(1, int(visible_width)))

    def _on_scrollbar_changed(self, value: int) -> None:
        pps = self._canvas._view.pixels_per_second
        self._canvas.set_scroll_offset(Fraction(value) / pps)
