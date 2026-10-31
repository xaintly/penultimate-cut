import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, QUrl
from PySide6.QtWidgets import QListWidget

from penultimate_cut.model.media_ops import move_media
from penultimate_cut.model.project import MediaRef
from penultimate_cut.ui.media_bin import (
    _ICON_SIZE,
    MEDIA_MIME_TYPE,
    MediaBin,
    MediaBinEntry,
    thumbnail_icon,
)


def pump_until(app, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def owned_bin() -> MediaBin:
    """A bin with something playing the owner's part, as MainWindow does in
    the app through undoable edits: imports are added to its list, and moves
    are made."""
    media_bin = MediaBin()
    media: list[MediaRef] = []

    def add(media_ref: MediaRef, _batch: int) -> None:
        media.append(media_ref)
        media_bin.show_media(media)

    def move(from_index: int, before_index: int) -> None:
        media[:] = move_media(media, from_index, before_index)
        media_bin.show_media(media)

    media_bin.media_imported.connect(add)
    media_bin.move_requested.connect(move)
    return media_bin


def has_thumbnail(media_bin: MediaBin) -> bool:
    entries = media_bin.entries
    return len(entries) == 1 and entries[0].thumbnail_path is not None


class _StubMimeData:
    def __init__(self, urls: list[QUrl]) -> None:
        self._urls = urls

    def hasUrls(self) -> bool:
        return bool(self._urls)

    def urls(self) -> list[QUrl]:
        return self._urls


class _StubDropEvent:
    def __init__(self, urls: list[QUrl]) -> None:
        self._mime_data = _StubMimeData(urls)
        self.accepted = False

    def mimeData(self) -> _StubMimeData:
        return self._mime_data

    def acceptProposedAction(self) -> None:
        self.accepted = True


def test_import_video_file_adds_entry_with_thumbnail(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_video.mp4", duration=2.0, fps=30, size="320x240")
    media_bin = owned_bin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: has_thumbnail(media_bin))

        entry = media_bin.entries[0]
        assert entry.media_ref.path == source
        assert entry.media_ref.has_video and entry.media_ref.has_audio
        assert (entry.media_ref.width, entry.media_ref.height) == (320, 240)
        assert entry.thumbnail_path is not None
        assert entry.thumbnail_path.exists()
        assert media_bin._list.count() == 1
    finally:
        media_bin.shutdown()


def test_import_audio_only_file_has_no_thumbnail(qapp, make_audio_clip) -> None:
    source = make_audio_clip("mb_audio.m4a", duration=2.0)
    media_bin = owned_bin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

        entry = media_bin.entries[0]
        assert entry.media_ref.has_audio and not entry.media_ref.has_video
        assert entry.thumbnail_path is None
    finally:
        media_bin.shutdown()


def test_import_still_image_is_labeled_as_image(qapp, make_still_image) -> None:
    source = make_still_image("mb_still.png", size="320x240")
    media_bin = owned_bin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: has_thumbnail(media_bin))

        entry = media_bin.entries[0]
        assert entry.media_ref.is_still_image
        assert entry.thumbnail_path is not None
        assert "Image" in media_bin._list.item(0).text()
    finally:
        media_bin.shutdown()


def test_import_nonexistent_file_adds_failed_placeholder(qapp, tmp_path: Path) -> None:
    media_bin = owned_bin()
    try:
        missing = tmp_path / "does_not_exist.mp4"
        media_bin.import_files([missing])
        assert pump_until(qapp, lambda: media_bin._list.count() == 1)

        assert media_bin.entries == []
        item = media_bin._list.item(0)
        assert "Couldn't import" in item.text()
        assert item.toolTip() != ""
    finally:
        media_bin.shutdown()


def test_imports_are_asked_for_not_listed_by_the_bin_itself(qapp, make_video_clip) -> None:
    """The bin shows the project's media; adding to it is the owner's edit.
    Files imported together come with the same batch number."""
    first = make_video_clip("mb_signal_a.mp4", duration=1.0, fps=30, size="320x240")
    second = make_video_clip("mb_signal_b.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = MediaBin()
    received: list[tuple[MediaRef, int]] = []
    media_bin.media_imported.connect(lambda media_ref, batch: received.append((media_ref, batch)))
    try:
        media_bin.import_files([first, second])
        assert pump_until(qapp, lambda: len(received) == 2)
        assert {media_ref.path for media_ref, _ in received} == {first, second}
        assert received[0][1] == received[1][1]
        assert media_bin.entries == []

        media_bin.import_files([first])
        assert pump_until(qapp, lambda: len(received) == 3)
        assert received[2][1] != received[0][1]
    finally:
        media_bin.shutdown()


def test_double_click_emits_media_double_clicked(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_dclick.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = owned_bin()
    received: list[MediaBinEntry] = []
    media_bin.media_double_clicked.connect(received.append)
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: media_bin._list.count() == 1)

        item = media_bin._list.item(0)
        media_bin._list.itemDoubleClicked.emit(item)
        qapp.processEvents()

        assert len(received) == 1
        assert received[0].media_ref.path == source
    finally:
        media_bin.shutdown()


def test_drag_enter_with_urls_accepts(qapp, tmp_path: Path) -> None:
    media_bin = owned_bin()
    try:
        event = _StubDropEvent([QUrl.fromLocalFile(str(tmp_path / "x.mp4"))])
        media_bin.dragEnterEvent(event)
        assert event.accepted is True
    finally:
        media_bin.shutdown()


def test_drag_enter_without_urls_does_not_accept(qapp) -> None:
    media_bin = owned_bin()
    try:
        event = _StubDropEvent([])
        media_bin.dragEnterEvent(event)
        assert event.accepted is False
    finally:
        media_bin.shutdown()


def test_drop_event_imports_dropped_files(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_drop.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = owned_bin()
    try:
        event = _StubDropEvent([QUrl.fromLocalFile(str(source))])
        media_bin.dropEvent(event)
        assert event.accepted is True
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)
        assert media_bin.entries[0].media_ref.path == source
    finally:
        media_bin.shutdown()


def test_clear_removes_all_entries(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_clear.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = owned_bin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

        media_bin.clear()

        assert media_bin.entries == []
        assert media_bin._list.count() == 0
    finally:
        media_bin.shutdown()


def test_show_media_lists_it_and_makes_its_thumbnail(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_add_ref.mp4", duration=2.0, fps=30, size="320x240")
    media_ref = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(2),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=320,
        height=240,
    )
    media_bin = owned_bin()
    try:
        media_bin.show_media([media_ref])
        assert len(media_bin.entries) == 1  # straight away; the thumbnail follows
        assert media_bin._list.count() == 1
        assert pump_until(qapp, lambda: has_thumbnail(media_bin))

        entry = media_bin.entries[0]
        assert entry.media_ref is media_ref
        assert entry.thumbnail_path is not None
        assert entry.thumbnail_path.exists()
    finally:
        media_bin.shutdown()


def test_show_media_leaves_the_list_alone_when_nothing_changed(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_add_ref_dup.mp4", duration=1.0, fps=30, size="320x240")
    media_ref = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(1),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    media_bin = owned_bin()
    try:
        media_bin.show_media([media_ref])
        assert pump_until(qapp, lambda: has_thumbnail(media_bin))
        item = media_bin._list.item(0)
        media_bin._list.setCurrentItem(item)

        media_bin.show_media([media_ref])

        assert media_bin._list.item(0) is item
        assert len(media_bin.entries) == 1

        other = MediaRef(
            path=source,
            relative_path=None,
            duration=Fraction(1),
            frame_rate=Fraction(30),
            has_video=True,
            has_audio=True,
        )
        media_bin.show_media([other, media_ref])
        assert [e.media_ref for e in media_bin.entries] == [other, media_ref]
        assert media_bin._list.currentRow() == 1  # still the same entry selected
    finally:
        media_bin.shutdown()


def test_list_items_do_not_move_freely(qapp) -> None:
    media_bin = owned_bin()
    try:
        assert media_bin._list.movement() == QListWidget.Movement.Static
        # setMovement(Static) also switches these off; moving items within
        # the bin needs them back on, after it.
        assert media_bin._list.dragEnabled()
        assert media_bin._list.viewport().acceptDrops()
    finally:
        media_bin.shutdown()


def test_dragging_an_entry_carries_its_media_id(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_drag.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = owned_bin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)
        entry = media_bin.entries[0]

        item = media_bin._list.item(0)
        mime = media_bin._list.mimeData([item])

        assert mime.hasFormat(MEDIA_MIME_TYPE)
        assert bytes(mime.data(MEDIA_MIME_TYPE)).decode("utf-8") == entry.media_ref.id
    finally:
        media_bin.shutdown()


def test_get_looks_up_an_entry_by_media_id(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_get.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = owned_bin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)
        entry = media_bin.entries[0]

        assert media_bin.get(entry.media_ref.id) == entry
        assert media_bin.get("no-such-id") is None
    finally:
        media_bin.shutdown()


def test_shutdown_is_safe_to_call(qapp) -> None:
    media_bin = owned_bin()
    media_bin.shutdown()  # must not raise or hang


def test_thumbnail_icons_keep_the_pictures_shape(qapp, make_still_image) -> None:
    """A portrait photo (or vertical video) is shown narrow and centered in
    the icon's landscape box, not stretched across it."""
    portrait = thumbnail_icon(make_still_image("mb_portrait.png", size="240x320"))
    image = portrait.pixmap(QSize(*_ICON_SIZE)).toImage()
    assert (image.width(), image.height()) == _ICON_SIZE
    middle = image.pixelColor(_ICON_SIZE[0] // 2, _ICON_SIZE[1] // 2)
    assert middle.alpha() == 255 and middle.blue() > 200  # the (blue) picture...
    assert image.pixelColor(5, _ICON_SIZE[1] // 2).alpha() == 0  # ...with clear space beside it
    assert image.pixelColor(_ICON_SIZE[0] - 5, _ICON_SIZE[1] // 2).alpha() == 0

    landscape = thumbnail_icon(make_still_image("mb_landscape.png", size="320x180"))
    edge = landscape.pixmap(QSize(*_ICON_SIZE)).toImage().pixelColor(2, _ICON_SIZE[1] // 2)
    assert edge.alpha() == 255  # fills the width


class _StubInternalDropEvent:
    """A drop of an item dragged within the list itself (Qt sets `source()`
    only for real drags, so this stands in for one)."""

    def __init__(self, source: object, pos: QPointF) -> None:
        self._source = source
        self._pos = pos
        self.drop_action: Qt.DropAction | None = None
        self.accepted = False

    def source(self) -> object:
        return self._source

    def position(self) -> QPointF:
        return self._pos

    def mimeData(self) -> _StubMimeData:
        return _StubMimeData([])

    def setDropAction(self, action: Qt.DropAction) -> None:
        self.drop_action = action

    def accept(self) -> None:
        self.accepted = True


def _bin_names(media_bin: MediaBin) -> list[str]:
    return [
        media_bin._list.item(row).text().split("\n")[0] for row in range(media_bin._list.count())
    ]


def test_dragging_an_entry_within_the_bin_moves_it_between_others(qapp, make_still_image) -> None:
    media_bin = owned_bin()
    try:
        media_bin.resize(800, 300)
        media_bin.show()
        for name in ("mb_order_a.png", "mb_order_b.png", "mb_order_c.png"):
            media_bin.import_files([make_still_image(name)])
            assert pump_until(qapp, lambda n=name: n in _bin_names(media_bin))
        assert _bin_names(media_bin) == ["mb_order_a.png", "mb_order_b.png", "mb_order_c.png"]
        listing = media_bin._list

        # Drop "a" on the right half of "c": it goes after "c".
        listing.setCurrentRow(0)
        rect_c = listing.visualItemRect(listing.item(2))
        event = _StubInternalDropEvent(listing, QPointF(rect_c.right() - 5, rect_c.center().y()))
        listing.dropEvent(event)  # type: ignore[arg-type]
        assert _bin_names(media_bin) == ["mb_order_b.png", "mb_order_c.png", "mb_order_a.png"]
        assert event.accepted
        # Copy, not Move: Qt deletes the dragged item after a Move.
        assert event.drop_action == Qt.DropAction.CopyAction
        assert listing.currentRow() == 2

        # Drop it back on the left half of "b": it goes before "b".
        rect_b = listing.visualItemRect(listing.item(0))
        listing.dropEvent(
            _StubInternalDropEvent(listing, QPointF(rect_b.left() + 5, rect_b.center().y()))
        )  # type: ignore[arg-type]
        assert _bin_names(media_bin) == ["mb_order_a.png", "mb_order_b.png", "mb_order_c.png"]
        assert len(media_bin.entries) == 3

        # Dropping it where it already is asks for nothing.
        moves: list[tuple[int, int]] = []
        media_bin.move_requested.connect(lambda f, b: moves.append((f, b)))
        listing.setCurrentRow(0)
        listing.dropEvent(
            _StubInternalDropEvent(listing, QPointF(rect_b.right() - 5, rect_b.center().y()))
        )  # type: ignore[arg-type]
        assert moves == []
    finally:
        media_bin.shutdown()


def test_drop_position_past_the_last_entry_means_the_end(qapp, make_still_image) -> None:
    media_bin = owned_bin()
    try:
        media_bin.resize(800, 300)
        media_bin.show()
        media_bin.import_files([make_still_image("mb_end_a.png")])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)
        listing = media_bin._list
        rect = listing.visualItemRect(listing.item(0))
        assert listing.insert_row_at(QPoint(rect.right() + 400, rect.center().y())) == 1
        assert listing.insert_row_at(rect.topLeft()) == 0
    finally:
        media_bin.shutdown()
