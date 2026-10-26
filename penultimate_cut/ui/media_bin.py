"""Media bin: import media, probe it, generate a thumbnail, and list it.

Import runs on a background thread pool -- ffprobe/ffmpeg calls are I/O-bound
subprocess work, not CPU-bound Python, so threads (not processes) are the
right tool -- so the UI never blocks per CLAUDE.md's "UI never blocks" rule.
Worker threads never touch Qt widgets directly; a result comes back through
a Qt signal, which is safe to emit cross-thread (Qt queues the connected
slot onto the receiving object's thread automatically).

Scoped to importing *into* the bin. Dragging *out* to the timeline goes
through MEDIA_MIME_TYPE: `_MediaListWidget.mimeData()` below puts the
dragged entry's id in that format rather than relying on Qt's own
internal (and implementation-specific) model-index MIME format, so
anything else -- `ui/timeline/widget.py`, currently -- can read it without
needing to know this is a `QListWidget` at all. `media_double_clicked` is
the other way to do the same thing.

The list uses `Movement.Static`: `QListView`'s default for `IconMode` is
free placement (drag an icon anywhere in the box, like arranging shapes in
a photo editor), which has no purpose here -- the bin has no concept of a
meaningful order -- and just looks broken. Static keeps items laid out in
import order while leaving the *outgoing* drag (a completely independent
Qt setting from in-view movement) untouched.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon
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
    """None for audio-only media, which has no frame to show a thumbnail of."""


class _ImportSignals(QObject):
    succeeded = Signal(object)  # MediaBinEntry
    failed = Signal(str, str)  # path, error message


class _MediaListWidget(QListWidget):
    """Overrides outgoing-drag MIME data to carry MEDIA_MIME_TYPE instead of
    (or alongside) Qt's own internal format -- see module docstring."""

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        mime = super().mimeData(items)
        if items:
            media_id = items[0].data(Qt.ItemDataRole.UserRole)
            if media_id:
                mime.setData(MEDIA_MIME_TYPE, str(media_id).encode("utf-8"))
        return mime


class MediaBin(QWidget):
    media_added = Signal(object)  # MediaBinEntry
    media_double_clicked = Signal(object)  # MediaBinEntry
    remove_requested = Signal(object)  # MediaBinEntry
    """The user asked to remove this media (Delete key or right-click). The
    bin doesn't remove it itself: the owner decides (it may be on the
    timeline, which needs a confirmation and an undoable edit), then calls
    `remove()`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-import")
        self._signals = _ImportSignals()
        self._signals.succeeded.connect(self._on_import_succeeded)
        self._signals.failed.connect(self._on_import_failed)
        self._entries: dict[str, MediaBinEntry] = {}
        self._building: set[str] = set()
        """Media ids whose entry is still being built on a worker thread."""

        self._list = _MediaListWidget()
        self._list.setViewMode(QListWidget.ViewMode.IconMode)
        self._list.setIconSize(QSize(*_ICON_SIZE))
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setMovement(QListWidget.Movement.Static)
        self._list.setWordWrap(True)
        self._list.setDragEnabled(True)
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
        return list(self._entries.values())

    def get(self, media_id: str) -> MediaBinEntry | None:
        return self._entries.get(media_id)

    def clear(self) -> None:
        """Remove every entry -- e.g. when a different project is opened,
        whose media bears no relation to whatever was in the bin before."""
        self._entries.clear()
        self._list.clear()

    def remove(self, media_id: str) -> None:
        self._entries.pop(media_id, None)
        for row in range(self._list.count()):
            if self._list.item(row).data(Qt.ItemDataRole.UserRole) == media_id:
                self._list.takeItem(row)
                return

    def has_focus(self) -> bool:
        return self._list.hasFocus()

    def request_remove_selected(self) -> None:
        """Delete key: ask to remove the selected media (see `remove_requested`)."""
        item = self._list.currentItem()
        if item is not None and item.isSelected():
            self._request_remove(item)

    def _request_remove(self, item: QListWidgetItem) -> None:
        media_id = item.data(Qt.ItemDataRole.UserRole)
        entry = self._entries.get(media_id) if media_id else None
        if entry is None:
            # A failed import ("Couldn't import ...") -- nothing to ask about.
            self._list.takeItem(self._list.row(item))
            return
        self.remove_requested.emit(entry)

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
        for path in paths:
            self._executor.submit(self._import_one, path)

    def add_media_ref(self, media_ref: MediaRef) -> None:
        """Add a `MediaRef` whose probed info is already known -- e.g. one
        belonging to a clip in a project that was just opened -- without
        re-probing it. Still generates a thumbnail asynchronously like a
        normal import. A no-op if this media is already in the bin."""
        if media_ref.id in self._entries or media_ref.id in self._building:
            return
        self._building.add(media_ref.id)
        self._executor.submit(self._build_entry, media_ref)

    def _import_one(self, path: Path) -> None:
        """Runs on a worker thread -- no Qt widget access here."""
        try:
            info = probe(path)
        except ProbeError as exc:
            self._signals.failed.emit(str(path), str(exc))
            return
        self._build_entry(_media_ref_from_info(path, info))

    def _build_entry(self, media_ref: MediaRef) -> None:
        """Runs on a worker thread -- no Qt widget access here."""
        try:
            thumb_path = (
                thumbnail(
                    media_ref.path, media_ref.duration, is_still_image=media_ref.is_still_image
                )
                if media_ref.has_video
                else None
            )
            entry = MediaBinEntry(media_ref=media_ref, thumbnail_path=thumb_path)
            self._signals.succeeded.emit(entry)
        except ThumbnailError as exc:
            self._signals.failed.emit(str(media_ref.path), str(exc))

    # --- Qt slots, main thread only -------------------------------------------

    def _on_import_succeeded(self, entry: MediaBinEntry) -> None:
        self._building.discard(entry.media_ref.id)
        if entry.media_ref.id in self._entries:
            return
        self._entries[entry.media_ref.id] = entry
        icon = QIcon(str(entry.thumbnail_path)) if entry.thumbnail_path else self._audio_icon()
        item = QListWidgetItem(icon, _label_for(entry.media_ref))
        item.setData(Qt.ItemDataRole.UserRole, entry.media_ref.id)
        self._list.addItem(item)
        self.media_added.emit(entry)

    def _on_import_failed(self, path: str, message: str) -> None:
        item = QListWidgetItem(f"Couldn't import {Path(path).name}")
        item.setToolTip(message)
        self._list.addItem(item)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        media_id = item.data(Qt.ItemDataRole.UserRole)
        entry = self._entries.get(media_id) if media_id else None
        if entry is not None:
            self.media_double_clicked.emit(entry)

    def _audio_icon(self) -> QIcon:
        return self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)

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
