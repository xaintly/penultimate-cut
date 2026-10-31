"""Media bin: import media, probe it, generate a thumbnail, and list it.

The bin shows the project's media (`Project.media`), in the project's
order, but never changes it itself: an import, a reorder, or a removal is
asked for through a signal (`media_imported`, `move_requested`,
`remove_requested`), the owner makes it an undoable edit, and then hands
the bin the project's new list through `show_media()`. That's what lets
the bin be saved with the project and come back after a crash.

Import runs on a background thread pool -- ffprobe/ffmpeg calls are I/O-bound
subprocess work, not CPU-bound Python, so threads (not processes) are the
right tool -- so the UI never blocks per CLAUDE.md's "UI never blocks" rule.
Worker threads never touch Qt widgets directly; a result comes back through
a Qt signal, which is safe to emit cross-thread (Qt queues the connected
slot onto the receiving object's thread automatically). An entry appears
as soon as its file has been probed; its thumbnail fills in when made.

Dragging *out* to the timeline goes through MEDIA_MIME_TYPE:
`_MediaListWidget.mimeData()` below puts the dragged entry's id in that
format rather than relying on Qt's own internal (and
implementation-specific) model-index MIME format, so anything else --
`ui/timeline/widget.py`, currently -- can read it without needing to know
this is a `QListWidget` at all. `media_double_clicked` is the other way to
do the same thing.

The list uses `Movement.Static`: `QListView`'s default for `IconMode` is
free placement (drag an icon anywhere in the box, like arranging shapes in
a photo editor), which just looks broken. Static keeps items on their grid
while leaving the *outgoing* drag (a completely independent Qt setting from
in-view movement) untouched. Dragging an item within the bin moves it
between the others instead -- see `_MediaListWidget`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from penultimate_cut.media.probe import MediaInfo, ProbeError, probe
from penultimate_cut.media.thumbnails import ThumbnailError, thumbnail
from penultimate_cut.model.project import MediaRef
from penultimate_cut.ui.format import format_duration

MEDIA_MIME_TYPE = "application/x-penultimate-cut-media-id"
"""Dragging an entry out of the media bin carries its MediaRef.id as plain
UTF-8 bytes under this MIME type -- a small, stable contract any drop
target can read, rather than Qt's internal item-model MIME format."""

_ICON_SIZE = (140, 90)
_FILE_FILTER = (
    "Media files (*.mp4 *.mov *.avi *.wmv *.mpg *.mpeg *.mkv *.webm *.m4v "
    "*.mp3 *.wav *.m4a *.aac *.flac *.ogg "
    "*.jpg *.jpeg *.png *.bmp *.gif *.tiff);;All files (*)"
)


@dataclass
class MediaBinEntry:
    media_ref: MediaRef
    thumbnail_path: Path | None
    """None for audio-only media, which has no frame to show a thumbnail
    of, and while the thumbnail is still being made (or couldn't be)."""


class _ImportSignals(QObject):
    imported = Signal(object, int)  # MediaRef, batch
    failed = Signal(str, str)  # path, error message
    thumbnail_done = Signal(str, object, str)  # media id, Path | None, error ("" if none)


class _MediaListWidget(QListWidget):
    """Overrides outgoing-drag MIME data to carry MEDIA_MIME_TYPE instead of
    (or alongside) Qt's own internal format -- see module docstring.

    Also lets an item be dragged within the list to between two others, with
    a bar showing where it will go, and asks for that move through
    `row_move_requested`. Qt's own internal move does this only in its list
    (not icon) mode. Files dragged in from outside are passed on through
    `files_dropped`."""

    files_dropped = Signal(list)  # list[Path]
    row_move_requested = Signal(int, int)  # from row, before row

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(*_ICON_SIZE))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setWordWrap(True)
        # These two set drag-enabled and the viewport's accept-drops to match
        # (Static: neither), so they come first and the drag setup after.
        self.setMovement(QListWidget.Movement.Static)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)  # Qt's indicator is for its own modes; see paintEvent
        self._drop_row: int | None = None
        """Where a drag within the list would put its item, while one is over it."""

    def insert_row_at(self, pos: QPoint) -> int:
        """The row an item dropped at `pos` (viewport coordinates) goes to:
        before the nearest item, or after it if `pos` is past its middle.
        Nearest means on the nearest line of the grid first."""
        nearest: tuple[int, int, int] | None = None  # (dy, dx, row)
        for row in range(self.count()):
            rect = self.visualItemRect(self.item(row))
            dx = max(rect.left() - pos.x(), 0, pos.x() - rect.right())
            dy = max(rect.top() - pos.y(), 0, pos.y() - rect.bottom())
            if nearest is None or (dy, dx) < nearest[:2]:
                nearest = (dy, dx, row)
        if nearest is None:
            return 0
        row = nearest[2]
        return row if pos.x() < self.visualItemRect(self.item(row)).center().x() else row + 1

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.source() is self:
            super().dragMoveEvent(event)  # scrolls a long bin near its edges
            self._set_drop_row(self.insert_row_at(event.position().toPoint()))
            event.setDropAction(Qt.DropAction.CopyAction)  # Move would delete the item
            event.accept()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        super().dragLeaveEvent(event)
        self._set_drop_row(None)

    def dropEvent(self, event: QDropEvent) -> None:
        self.stopAutoScroll()
        self._set_drop_row(None)
        if event.source() is self:
            if self.currentRow() >= 0:
                before_row = self.insert_row_at(event.position().toPoint())
                self.row_move_requested.emit(self.currentRow(), before_row)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        elif event.mimeData().hasUrls():
            paths = [
                Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()
            ]
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def _set_drop_row(self, row: int | None) -> None:
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._drop_row is None or self.count() == 0:
            return
        if self._drop_row < self.count():
            rect = self.visualItemRect(self.item(self._drop_row))
            x = rect.left()
        else:
            rect = self.visualItemRect(self.item(self.count() - 1))
            x = rect.right()
        painter = QPainter(self.viewport())
        painter.fillRect(x - 1, rect.top(), 3, rect.height(), self.palette().highlight())
        painter.end()

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        mime = super().mimeData(items)
        if items:
            media_id = items[0].data(Qt.ItemDataRole.UserRole)
            if media_id:
                mime.setData(MEDIA_MIME_TYPE, str(media_id).encode("utf-8"))
        return mime


class MediaBin(QWidget):
    media_imported = Signal(object, int)  # MediaRef, batch
    """A file was probed and is ready to add to the project. Files imported
    together share a `batch` number, so they can be one undo step."""
    media_double_clicked = Signal(object)  # MediaBinEntry
    remove_requested = Signal(object)  # MediaBinEntry
    """The user asked to remove this media (Delete key or right-click). The
    owner decides (it may be on the timeline, which needs a confirmation)."""
    move_requested = Signal(int, int)  # from index, before index
    """The user dragged the entry at `from` to just before the one at
    `before` (`len(entries)` for the end); only sent for an actual move."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-import")
        self._signals = _ImportSignals()
        self._signals.imported.connect(self.media_imported.emit)
        self._signals.failed.connect(self._on_import_failed)
        self._signals.thumbnail_done.connect(self._on_thumbnail_done)
        self._media: list[MediaRef] = []
        self._thumbnails: dict[str, Path | None] = {}
        """Made (or failed: None) thumbnails, by media id -- kept while an
        entry is gone, so undoing its removal doesn't make it again."""
        self._thumbnail_errors: dict[str, str] = {}
        self._pending: set[str] = set()
        self._failed: list[tuple[str, str]] = []
        """Imports that failed: (label, error), shown after the media. Not
        part of the project; they go when removed or another project opens."""
        self._batch = 0

        self._list = _MediaListWidget()
        self._list.files_dropped.connect(self.import_files)
        self._list.row_move_requested.connect(self._on_row_move_requested)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list, 1)

    def shutdown(self) -> None:
        """Stop accepting new import work. Call explicitly before this widget
        is destroyed, same reasoning as PreviewWidget.shutdown()."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def entries(self) -> list[MediaBinEntry]:
        return [MediaBinEntry(m, self._thumbnails.get(m.id)) for m in self._media]

    def get(self, media_id: str) -> MediaBinEntry | None:
        return next((entry for entry in self.entries if entry.media_ref.id == media_id), None)

    def show_media(self, media: list[MediaRef]) -> None:
        """List `media` (the project's bin), in that order. Cheap when
        nothing changed, so it can follow every edit."""
        for media_ref in media:
            self._request_thumbnail(media_ref)
        unchanged = [m.id for m in media] == [m.id for m in self._media]
        self._media = list(media)
        if not unchanged:
            self._rebuild()

    def clear(self) -> None:
        """Forget everything -- e.g. when a different project is opened,
        whose media bears no relation to whatever was in the bin before."""
        self._failed.clear()
        self._thumbnails.clear()
        self._thumbnail_errors.clear()
        self._media = []
        self._rebuild()

    def has_focus(self) -> bool:
        return self._list.hasFocus()

    def request_remove_selected(self) -> None:
        """Delete key: ask to remove the selected media (see `remove_requested`)."""
        item = self._list.currentItem()
        if item is not None and item.isSelected():
            self._request_remove(item)

    def _rebuild(self) -> None:
        current = self._list.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        scroll = self._list.verticalScrollBar().value()
        self._list.clear()
        for media_ref in self._media:
            item = QListWidgetItem(self._icon_for(media_ref), _label_for(media_ref))
            item.setData(Qt.ItemDataRole.UserRole, media_ref.id)
            item.setToolTip(self._thumbnail_errors.get(media_ref.id, ""))
            self._list.addItem(item)
            if media_ref.id == current_id:
                self._list.setCurrentItem(item)
        for label, error in self._failed:
            item = QListWidgetItem(label)
            item.setToolTip(error)
            self._list.addItem(item)
        self._list.verticalScrollBar().setValue(scroll)

    def _request_remove(self, item: QListWidgetItem) -> None:
        media_id = item.data(Qt.ItemDataRole.UserRole)
        entry = self.get(media_id) if media_id else None
        if entry is None:
            # A failed import ("Couldn't import ...") -- nothing to ask about.
            row = self._list.row(item) - len(self._media)
            if 0 <= row < len(self._failed):
                del self._failed[row]
            self._list.takeItem(self._list.row(item))
            return
        self.remove_requested.emit(entry)

    def _on_row_move_requested(self, from_row: int, before_row: int) -> None:
        count = len(self._media)
        if from_row >= count:
            return  # a failed import; those stay at the end
        before = min(before_row, count)
        if before not in (from_row, from_row + 1):
            self.move_requested.emit(from_row, before)

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        # Parented to the top-level window: see TimelineWidget's menu.
        menu = QMenu(self.window())
        menu.addAction("Remove from project").triggered.connect(lambda: self._request_remove(item))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    # --- import --------------------------------------------------------------

    def choose_files(self) -> None:
        """Open a native file picker and import whatever's selected. The main
        window's own "Import" button calls this -- this widget has no button
        of its own, per CLAUDE.md's one-Import-button UI layout."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Import media", filter=_FILE_FILTER)
        self.import_files([Path(p) for p in paths])

    def import_files(self, paths: list[Path]) -> None:
        if not paths:
            return
        self._batch += 1
        for path in paths:
            self._executor.submit(self._import_one, path, self._batch)

    def _import_one(self, path: Path, batch: int) -> None:
        """Runs on a worker thread -- no Qt widget access here."""
        try:
            info = probe(path)
        except ProbeError as exc:
            self._signals.failed.emit(str(path), str(exc))
            return
        self._signals.imported.emit(_media_ref_from_info(path, info), batch)

    def _request_thumbnail(self, media_ref: MediaRef) -> None:
        if media_ref.id in self._thumbnails or media_ref.id in self._pending:
            return
        if not media_ref.has_video:
            self._thumbnails[media_ref.id] = None
            return
        self._pending.add(media_ref.id)
        self._executor.submit(self._make_thumbnail, media_ref)

    def _make_thumbnail(self, media_ref: MediaRef) -> None:
        """Runs on a worker thread -- no Qt widget access here."""
        try:
            made = thumbnail(
                media_ref.path, media_ref.duration, is_still_image=media_ref.is_still_image
            )
        except ThumbnailError as exc:
            self._signals.thumbnail_done.emit(media_ref.id, None, str(exc))
        else:
            self._signals.thumbnail_done.emit(media_ref.id, made, "")

    # --- Qt slots, main thread only -------------------------------------------

    def _on_thumbnail_done(self, media_id: str, made: Path | None, error: str) -> None:
        self._pending.discard(media_id)
        self._thumbnails[media_id] = made
        if error:
            self._thumbnail_errors[media_id] = f"Couldn't show this file: {error}"
        for row in range(len(self._media)):
            item = self._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == media_id:
                item.setIcon(self._icon_for(self._media[row]))
                item.setToolTip(self._thumbnail_errors.get(media_id, ""))

    def _on_import_failed(self, path: str, message: str) -> None:
        label = f"Couldn't import {Path(path).name}"
        self._failed.append((label, message))
        item = QListWidgetItem(label)
        item.setToolTip(message)
        self._list.addItem(item)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        media_id = item.data(Qt.ItemDataRole.UserRole)
        entry = self.get(media_id) if media_id else None
        if entry is not None:
            self.media_double_clicked.emit(entry)

    def _icon_for(self, media_ref: MediaRef) -> QIcon:
        if not media_ref.has_video:
            return self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        made = self._thumbnails.get(media_ref.id)
        if made is not None:
            return thumbnail_icon(made)
        if media_ref.id in self._thumbnail_errors:
            return self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        blank = QPixmap(*_ICON_SIZE)  # still being made; keeps the grid's shape
        blank.fill(Qt.GlobalColor.transparent)
        return QIcon(blank)

    # --- drag and drop, files coming in from outside the app ------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.import_files(paths)
            event.acceptProposedAction()


def _media_ref_from_info(path: Path, info: MediaInfo) -> MediaRef:
    return MediaRef(
        path=path,
        relative_path=None,
        duration=info.duration,
        frame_rate=info.frame_rate,
        has_video=info.has_video,
        has_audio=info.has_audio,
        width=info.width,
        height=info.height,
    )


def _label_for(media_ref: MediaRef) -> str:
    name = media_ref.path.name
    if media_ref.is_still_image:
        return f"{name}\nImage"
    return f"{name}\n{format_duration(media_ref.duration)}"


def thumbnail_icon(path: Path) -> QIcon:
    """The thumbnail at its own shape, centered in the icon's box. A plain
    `QIcon(path)` stretches it to fill the box, squashing vertical video and
    portrait photos."""
    picture = QPixmap(str(path)).scaled(
        QSize(*_ICON_SIZE),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QPixmap(*_ICON_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(
        (canvas.width() - picture.width()) // 2, (canvas.height() - picture.height()) // 2, picture
    )
    painter.end()
    return QIcon(canvas)
