import time
from fractions import Fraction

import pytest
from PySide6.QtWidgets import QApplication

from penultimate_cut.model.project import Clip, MediaRef, Project, Track
from penultimate_cut.ui.preview_widget import PreviewWidget


def make_project(source, duration: float = 2.0) -> Project:
    media = MediaRef(
        path=source,
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=320,
        height=240,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(str(duration)))
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])


def pump(app: QApplication, seconds: float) -> None:
    """Process Qt events for `seconds` -- lets the queued position_changed
    signal (emitted from mpv's own thread) actually reach the widget."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture(scope="module")
def widget(qapp):
    """One shared PreviewWidget for the whole module.

    Not just a speed optimization: creating and tearing down a *second*
    embedded-mpv instance in the same process reliably hangs inside
    libmpv/Mesa's GL teardown under this environment's software renderer
    (reproduced in isolation, unrelated to anything in our own code). The
    real app only ever creates one Player for its entire lifetime anyway,
    so sharing one here across tests matches production shape rather than
    working around a test-only problem.
    """
    created = PreviewWidget()
    created.show()
    qapp.processEvents()
    try:
        yield created
    finally:
        created.shutdown()


def test_widget_embeds_real_window_and_loads_project(qapp, widget, make_video_clip) -> None:
    source = make_video_clip("pw_a.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)

    assert int(widget._video_surface.winId()) != 0

    widget.load_project(project)
    assert widget.player.wait_until_ready(timeout=8)
    pump(qapp, 1.0)

    assert widget._duration == Fraction(2)
    assert widget._slider.maximum() > 0
    assert widget._time_label.text() != "00:00 / 00:00"


def test_load_project_starts_paused_not_autoplaying(qapp, widget, make_video_clip) -> None:
    """mpv autoplays a freshly loaded file by default; `load_project` must
    override that so an edit (every one of which reloads the EDL) doesn't
    silently resume playback, and so the very first press of Play actually
    starts it instead of pausing an already-playing video the button's own
    label never reflected."""
    source = make_video_clip("pw_autoplay.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)

    widget.load_project(project)
    assert widget.player.wait_until_ready(timeout=8)
    pump(qapp, 0.3)
    assert widget.player.is_playing is False
    assert widget._play_button.text() == "Play"

    widget._play_button.click()
    assert widget.player.is_playing is True
    position_before = widget.player.position
    pump(qapp, 0.5)
    assert widget.player.position > position_before


def test_play_pause_updates_button_text(widget, make_video_clip) -> None:
    source = make_video_clip("pw_b.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)

    widget.load_project(project)
    assert widget.player.wait_until_ready(timeout=8)
    widget.pause()
    assert widget._play_button.text() == "Play"

    widget.play()
    assert widget._play_button.text() == "Pause"

    widget.toggle_play_pause()
    assert widget._play_button.text() == "Play"


def test_frame_step_buttons_move_the_playhead(widget, make_video_clip) -> None:
    source = make_video_clip("pw_c.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)

    widget.load_project(project)
    assert widget.player.wait_until_ready(timeout=8)
    widget.pause()
    widget.seek(Fraction(1))
    time.sleep(0.2)
    before = widget.player.position

    widget._forward_button.click()
    time.sleep(0.2)
    after_forward = widget.player.position

    widget._back_button.click()
    time.sleep(0.2)
    after_back = widget.player.position

    assert before is not None and after_forward is not None and after_back is not None
    assert after_forward > before
    assert after_back < after_forward


def test_slider_drag_seeks_the_player(qapp, widget, make_video_clip) -> None:
    source = make_video_clip("pw_d.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)

    widget.load_project(project)
    assert widget.player.wait_until_ready(timeout=8)
    pump(qapp, 0.5)  # let the slider range populate from the real duration
    widget.pause()

    # Simulate a user drag via the private handlers directly -- Qt's
    # sliderMoved only fires from real mouse interaction, which isn't
    # practical to synthesize here for a slider wired to no visible parent.
    # setValue() first, matching what a real drag does to the slider itself,
    # since _on_slider_released() re-reads the slider's current value rather
    # than remembering what _on_slider_moved() was last called with.
    widget._on_slider_pressed()
    assert widget._dragging is True
    widget._slider.setValue(1500)  # 1.5s at the widget's slider resolution
    widget._on_slider_moved(1500)
    time.sleep(0.2)
    widget._on_slider_released()
    assert widget._dragging is False

    assert widget.player.position == Fraction("1.5")


def test_button_says_play_before_anything_is_loaded(qapp) -> None:
    # A fresh widget (the shared one has already played), with vo=null so a
    # second instance doesn't hit the GL-teardown hang `widget` describes.
    fresh = PreviewWidget(vo="null")
    try:
        pump(qapp, 0.3)
        assert fresh._play_button.text() == "Play"
    finally:
        fresh.shutdown()


def test_volume_slider_sets_player_volume_and_reports_changes(widget) -> None:
    reported: list[int] = []
    widget.volume_changed.connect(reported.append)
    try:
        widget.set_volume(40)
        assert widget.player.volume == 40
        assert reported == [40]
    finally:
        widget.volume_changed.disconnect(reported.append)
        widget.set_volume(100)
