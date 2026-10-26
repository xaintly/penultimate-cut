import time
from fractions import Fraction

import pytest

import penultimate_cut.preview.player as player_module
from penultimate_cut.model.project import Clip, MediaRef, Project, Track
from penultimate_cut.preview.edl import to_edl
from penultimate_cut.preview.player import Player


def make_project(source, duration: float = 3.0) -> Project:
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


class _FakeMPV:
    """Stands in for `mpv.MPV` so these two tests can check what `Player`
    passes it without touching real libmpv (and so they don't need a
    display/QApplication at all)."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def terminate(self) -> None:
        pass


def test_windows_embedding_pins_vo_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """vo=gpu-next (libmpv's current default) is known to render a fully
    black window when embedded via `wid=` on Windows -- see player.py's own
    comment. Windows-only, since this environment's real-GL test relies on
    the default and vo=gpu doesn't even initialize against its software
    renderer."""
    captured: dict[str, object] = {}
    monkeypatch.setattr(player_module.sys, "platform", "win32")
    monkeypatch.setattr(player_module.mpv, "MPV", lambda **kw: captured.update(kw) or _FakeMPV())

    Player(wid=12345).close()

    assert captured["wid"] == 12345
    assert captured["vo"] == "gpu"


def test_non_windows_embedding_leaves_default_vo(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(player_module.sys, "platform", "linux")
    monkeypatch.setattr(player_module.mpv, "MPV", lambda **kw: captured.update(kw) or _FakeMPV())

    Player(wid=12345).close()

    assert captured["wid"] == 12345
    assert "vo" not in captured


def test_load_edl_and_report_duration(make_video_clip) -> None:
    source = make_video_clip("player_a.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)

    with Player() as player:
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        assert player.duration == pytest.approx(Fraction(3), abs=Fraction(1, 10))


def test_seek_is_frame_exact(make_video_clip) -> None:
    source = make_video_clip("player_b.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)

    with Player() as player:
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        player.pause()
        player.seek(Fraction("1.5"))
        time.sleep(0.2)
        assert player.position == pytest.approx(Fraction("1.5"), abs=Fraction(1, 100))


def test_frame_step_advances_by_one_frame(make_video_clip) -> None:
    source = make_video_clip("player_c.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)

    with Player() as player:
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        player.pause()
        player.seek(Fraction(1))
        time.sleep(0.2)
        before = player.position
        player.frame_step("forward")
        time.sleep(0.2)
        after = player.position
        assert before is not None and after is not None
        assert after - before == pytest.approx(Fraction(1, 30), abs=Fraction(1, 1000))


def test_frame_step_backward(make_video_clip) -> None:
    source = make_video_clip("player_d.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)

    with Player() as player:
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        player.pause()
        player.seek(Fraction(1))
        time.sleep(0.2)
        before = player.position
        player.frame_step("backward")
        time.sleep(0.2)
        after = player.position
        assert before is not None and after is not None
        assert before - after == pytest.approx(Fraction(1, 30), abs=Fraction(1, 1000))


def test_play_pause_toggle(make_video_clip) -> None:
    source = make_video_clip("player_e.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)

    with Player() as player:
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        player.pause()
        assert not player.is_playing
        player.play()
        assert player.is_playing
        player.toggle_play_pause()
        assert not player.is_playing


def test_speed_change_is_pitch_corrected_by_default(make_video_clip) -> None:
    source = make_video_clip("player_f.mp4", duration=3.0, fps=30, size="320x240")
    project = make_project(source, duration=3.0)

    with Player() as player:
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        player.speed = Fraction(2)
        assert player.speed == Fraction(2)
        assert player._mpv.audio_pitch_correction is True


def test_reloading_edl_reuses_the_same_scratch_file(make_video_clip) -> None:
    source_a = make_video_clip("player_g1.mp4", duration=2.0, fps=30, size="320x240")
    source_b = make_video_clip("player_g2.mp4", duration=4.0, fps=30, size="320x240")

    with Player() as player:
        player.load_edl(to_edl(make_project(source_a, duration=2.0)))
        assert player.wait_until_ready(timeout=8)
        first_edl_path = player._edl_path

        player.load_edl(to_edl(make_project(source_b, duration=4.0)))
        assert player.wait_until_ready(timeout=8)

        assert player._edl_path == first_edl_path
        assert player.duration == pytest.approx(Fraction(4), abs=Fraction(1, 10))


def test_position_changed_callback_fires(make_video_clip) -> None:
    source = make_video_clip("player_h.mp4", duration=2.0, fps=30, size="320x240")
    project = make_project(source, duration=2.0)

    positions: list[Fraction | None] = []
    with Player() as player:
        player.on_position_changed(positions.append)
        player.load_edl(to_edl(project))
        assert player.wait_until_ready(timeout=8)
        time.sleep(0.5)

    assert len(positions) > 0


def test_wait_until_ready_returns_false_on_bad_edl() -> None:
    with Player() as player:
        player.load_edl("# mpv EDL v0\n")  # no segments at all
        assert player.wait_until_ready(timeout=2) is False


def test_play_after_reaching_the_end_restarts(make_video_clip) -> None:
    # Regression: mpv used to unload the file at the end of the timeline and
    # go idle, after which play and seek silently did nothing at all.
    source = make_video_clip("player_eof.mp4", duration=3.0, fps=30, size="320x240")
    with Player() as player:
        player.load_edl(to_edl(make_project(source, duration=1.0)))
        assert player.wait_until_ready()
        player.play()
        deadline = time.monotonic() + 5
        while player.is_playing and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not player.is_playing  # paused itself on the last frame
        assert player.position is not None

        player.play()
        time.sleep(0.3)
        assert player.is_playing
        position = player.position
        assert position is not None and position < Fraction(1)


def test_load_edl_can_start_mid_timeline(make_video_clip) -> None:
    # Reloading after an edit must keep the playhead where it was, not
    # snap back to 0:00 (a seek right after loading would be dropped).
    source = make_video_clip("player_start.mp4", duration=3.0, fps=30, size="320x240")
    with Player() as player:
        player.load_edl(to_edl(make_project(source, duration=3.0)), start=Fraction(2))
        assert player.wait_until_ready()
        position = player.position
        assert position is not None and abs(position - 2) < Fraction(1, 10)


def test_volume_is_clamped_to_0_100() -> None:
    with Player() as player:
        player.volume = 35
        assert player.volume == 35
        player.volume = 150
        assert player.volume == 100
        player.volume = -5
        assert player.volume == 0
