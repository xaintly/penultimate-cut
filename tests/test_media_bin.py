import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QListWidget

from penultimate_cut.model.project import MediaRef
from penultimate_cut.ui.media_bin import MEDIA_MIME_TYPE, MediaBin, MediaBinEntry


def pump_until(app, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


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
    media_bin = MediaBin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

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
    media_bin = MediaBin()
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
    media_bin = MediaBin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

        entry = media_bin.entries[0]
        assert entry.media_ref.is_still_image
        assert entry.thumbnail_path is not None
        assert "Image" in media_bin._list.item(0).text()
    finally:
        media_bin.shutdown()


def test_import_nonexistent_file_adds_failed_placeholder(qapp, tmp_path: Path) -> None:
    media_bin = MediaBin()
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


def test_media_added_signal_fires_with_entry(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_signal.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = MediaBin()
    received: list[MediaBinEntry] = []
    media_bin.media_added.connect(received.append)
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(received) == 1)
        assert received[0].media_ref.path == source
    finally:
        media_bin.shutdown()


def test_double_click_emits_media_double_clicked(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_dclick.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = MediaBin()
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
    media_bin = MediaBin()
    try:
        event = _StubDropEvent([QUrl.fromLocalFile(str(tmp_path / "x.mp4"))])
        media_bin.dragEnterEvent(event)
        assert event.accepted is True
    finally:
        media_bin.shutdown()


def test_drag_enter_without_urls_does_not_accept(qapp) -> None:
    media_bin = MediaBin()
    try:
        event = _StubDropEvent([])
        media_bin.dragEnterEvent(event)
        assert event.accepted is False
    finally:
        media_bin.shutdown()


def test_drop_event_imports_dropped_files(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_drop.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = MediaBin()
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
    media_bin = MediaBin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

        media_bin.clear()

        assert media_bin.entries == []
        assert media_bin._list.count() == 0
    finally:
        media_bin.shutdown()


def test_add_media_ref_skips_probing_but_still_generates_thumbnail(qapp, make_video_clip) -> None:
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
    media_bin = MediaBin()
    try:
        media_bin.add_media_ref(media_ref)
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

        entry = media_bin.entries[0]
        assert entry.media_ref is media_ref
        assert entry.thumbnail_path is not None
        assert entry.thumbnail_path.exists()
    finally:
        media_bin.shutdown()


def test_add_media_ref_is_a_noop_for_an_already_present_id(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_add_ref_dup.mp4", duration=1.0, fps=30, size="320x240")
    media_ref = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(1),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    media_bin = MediaBin()
    try:
        media_bin.add_media_ref(media_ref)
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)

        media_bin.add_media_ref(media_ref)
        qapp.processEvents()

        assert len(media_bin.entries) == 1
    finally:
        media_bin.shutdown()


def test_list_items_do_not_move_freely(qapp) -> None:
    media_bin = MediaBin()
    try:
        assert media_bin._list.movement() == QListWidget.Movement.Static
    finally:
        media_bin.shutdown()


def test_dragging_an_entry_carries_its_media_id(qapp, make_video_clip) -> None:
    source = make_video_clip("mb_drag.mp4", duration=1.0, fps=30, size="320x240")
    media_bin = MediaBin()
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
    media_bin = MediaBin()
    try:
        media_bin.import_files([source])
        assert pump_until(qapp, lambda: len(media_bin.entries) == 1)
        entry = media_bin.entries[0]

        assert media_bin.get(entry.media_ref.id) is entry
        assert media_bin.get("no-such-id") is None
    finally:
        media_bin.shutdown()


def test_shutdown_is_safe_to_call(qapp) -> None:
    media_bin = MediaBin()
    media_bin.shutdown()  # must not raise or hang
