from fractions import Fraction
from pathlib import Path

import mpv
import pytest

from penultimate_cut.model.project import (
    Clip,
    Gap,
    MediaRef,
    Project,
    Title,
    Track,
    new_title_clip,
)
from penultimate_cut.preview.edl import (
    EDLError,
    generated_spans,
    preview_filters,
    to_edl,
)


def make_media(path: Path, *, width: int = 320, height: int = 240) -> MediaRef:
    return MediaRef(
        path=path,
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
        width=width,
        height=height,
    )


def play_duration(tmp_path: Path, edl_text: str, timeout: float = 8.0) -> float | None:
    """Write `edl_text` to disk and have a headless libmpv load it, returning
    the duration it reports (None if it never reached a playable state)."""
    edl_path = tmp_path / "preview.edl"
    edl_path.write_text(edl_text, encoding="utf-8", newline="\n")
    player = mpv.MPV(vo="null", ao="null", load_unsafe_playlists=True)
    try:
        player.play(str(edl_path))
        try:
            player.wait_until_playing(timeout=timeout)
        except Exception:
            return None
        return player.duration
    finally:
        player.terminate()


# --- text format (fast, no mpv) ---------------------------------------------


def test_header_is_first_line() -> None:
    project = Project(frame_rate=Fraction(30), tracks=[])
    assert to_edl(project).splitlines()[0] == "# mpv EDL v0"


def test_clip_line_has_escaped_file_and_named_params(tmp_path: Path) -> None:
    media_path = tmp_path / "clip.mp4"
    clip = Clip(media_ref=make_media(media_path), source_in=Fraction(2), source_out=Fraction(5))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    lines = to_edl(project).splitlines()
    expected_file_param = f"file=%{len(str(media_path).encode())}%{media_path}"
    assert lines == [
        "# mpv EDL v0",
        "!new_stream",
        f"{expected_file_param},start=2.000000,length=3.000000",
    ]


def test_empty_tracks_are_omitted() -> None:
    project = Project(
        frame_rate=Fraction(30),
        tracks=[Track(kind="video", items=[]), Track(kind="audio", items=[])],
    )
    assert to_edl(project) == "# mpv EDL v0\n"


def test_speed_changed_clip_raises(tmp_path: Path) -> None:
    media_path = tmp_path / "clip.mp4"
    clip = Clip(
        media_ref=make_media(media_path),
        source_in=Fraction(0),
        source_out=Fraction(4),
        speed=Fraction(2),
    )
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    with pytest.raises(EDLError):
        to_edl(project)


def test_still_image_clip_forces_start_zero() -> None:
    media = MediaRef(
        path=Path("/nonexistent/photo.jpg"),
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    # A reused still image can have a nonzero source_in in our model even
    # though "position within the image" is meaningless.
    clip = Clip(media_ref=media, source_in=Fraction(5), source_out=Fraction(8))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    line = to_edl(project).splitlines()[-1]
    assert ",start=0.000000,length=3.000000" in line


# --- actual playability, verified against a live libmpv --------------------


def test_clip_and_gap_play_with_correct_total_duration(tmp_path: Path, make_video_clip) -> None:
    video_path = make_video_clip("edl_a.mp4", duration=3.0, fps=30, size="320x240")
    media = make_media(video_path)
    clip1 = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    clip2 = Clip(media_ref=media, source_in=Fraction(1), source_out=Fraction(3))
    track = Track(kind="video", items=[clip1, Gap(duration=Fraction(1)), clip2])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    duration = play_duration(tmp_path, to_edl(project))

    assert duration is not None
    assert duration == pytest.approx(5.0, abs=0.05)


def test_still_image_clip_plays(tmp_path: Path, make_still_image) -> None:
    image_path = make_still_image("edl_still.png", size="320x240")
    media = MediaRef(
        path=image_path,
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(4))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    duration = play_duration(tmp_path, to_edl(project))

    assert duration is not None
    assert duration == pytest.approx(4.0, abs=0.05)


def test_second_audio_track_extends_total_duration(
    tmp_path: Path, make_video_clip, make_audio_clip
) -> None:
    video_path = make_video_clip("edl_video.mp4", duration=2.0, fps=30, size="320x240")
    audio_path = make_audio_clip("edl_audio.m4a", duration=5.0)

    video_clip = Clip(
        media_ref=make_media(video_path), source_in=Fraction(0), source_out=Fraction(2)
    )
    audio_media = MediaRef(
        path=audio_path,
        relative_path=None,
        duration=Fraction(5),
        frame_rate=None,
        has_video=False,
        has_audio=True,
    )
    audio_clip = Clip(media_ref=audio_media, source_in=Fraction(0), source_out=Fraction(5))

    project = Project(
        frame_rate=Fraction(30),
        tracks=[
            Track(kind="video", items=[video_clip]),
            Track(kind="audio", items=[audio_clip]),
        ],
    )

    duration = play_duration(tmp_path, to_edl(project))

    # The longer audio-track partition determines the overall duration.
    assert duration is not None
    assert duration == pytest.approx(5.0, abs=0.05)


# --- preview filters: clip volume/mute and fades ------------------------------


def test_preview_filters_are_empty_for_default_clips(tmp_path: Path) -> None:
    clip = Clip(media_ref=make_media(tmp_path / "a.mp4"), source_in=Fraction(0), source_out=2)
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    assert preview_filters(project) == ("", "")


def test_preview_filters_use_timeline_time(tmp_path: Path) -> None:
    media = make_media(tmp_path / "a.mp4")
    first = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2), volume=150)
    second = Clip(
        media_ref=media,
        source_in=Fraction(5),
        source_out=Fraction(8),
        muted=True,
        fade_in=True,
        fade_out=True,
    )
    items = [first, Gap(duration=Fraction(1)), second]  # `second` spans 3s-6s
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])

    video, audio = preview_filters(project)

    assert video == (
        "fade=t=in:st=3.000000:d=1.000000:enable='between(t,3.000000,4.000000)',"
        "fade=t=out:st=5.000000:d=1.000000:enable='between(t,5.000000,6.000000)'"
    )
    assert audio == (
        "volume=volume=1.500000:enable='between(t,0.000000,2.000000)',"
        "volume=volume=0.000000:enable='between(t,3.000000,6.000000)',"
        "afade=t=in:st=3.000000:d=1.000000:enable='between(t,3.000000,4.000000)',"
        "afade=t=out:st=5.000000:d=1.000000:enable='between(t,5.000000,6.000000)'"
    )


def test_preview_filters_fade_and_mute_live_playback(tmp_path: Path) -> None:
    """Plays a white, then a muted and faded-in clip through the real Player,
    recording every frame and the sound: the fade must land where the second
    clip starts on the timeline, not at its own time 0."""
    import subprocess
    import wave

    from PySide6.QtGui import QImage

    from penultimate_cut.preview.player import Player

    white = tmp_path / "white.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=white:s=64x48:r=30:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(white)],
        check=True,
    )  # fmt: skip
    media = make_media(white, width=64, height=48)
    loud = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    quiet = Clip(
        media_ref=media, source_in=Fraction(0), source_out=Fraction(2), muted=True, fade_in=True
    )
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[loud, quiet])])

    frames_dir, wav_path = tmp_path / "frames", tmp_path / "sound.wav"
    frames_dir.mkdir()
    player = Player(
        vo="image", vo_image_outdir=str(frames_dir), vo_image_format="png",
        ao="pcm", ao_pcm_file=str(wav_path), ao_pcm_waveheader=True, audio_format="s16",
        untimed=True, keep_open="no",
    )  # fmt: skip
    try:
        player.set_filters(*preview_filters(project))
        player.load_edl(to_edl(project))
        player._mpv.wait_for_playback()
    finally:
        player.close()

    frames = sorted(frames_dir.glob("*.png"))
    brightness = [QImage(str(f)).pixelColor(32, 24).red() for f in frames]
    assert len(frames) == 120
    assert min(brightness[:60]) > 240  # first clip untouched
    assert brightness[60] < 15  # black where the second clip starts...
    assert 100 < brightness[75] < 155  # ...half way after 0.5 s...
    assert min(brightness[95:]) > 240  # ...and full after its 1 s fade

    with wave.open(str(wav_path)) as sound:
        rate, channels = sound.getframerate(), sound.getnchannels()
        samples = memoryview(sound.readframes(sound.getnframes())).cast("h")
    first_clip = samples[: rate * channels * 19 // 10]  # to 1.9 s
    second_clip = samples[rate * channels * 21 // 10 :]  # from 2.1 s
    assert max(abs(s) for s in first_clip) > 1000
    assert max(abs(s) for s in second_clip) == 0  # muted


def test_pictures_play_as_generated_segments_with_silence() -> None:
    title_clip = new_title_clip(Title(text="Hello"))
    photo = MediaRef(
        path=Path("C:/My Photos, 2026/it's [one]; 100%.jpg"),
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    photo_clip = Clip(media_ref=photo, source_in=Fraction(4), source_out=Fraction(7))
    project = Project(
        frame_rate=Fraction(30000, 1001),
        tracks=[Track(kind="video", items=[title_clip, photo_clip])],
    )

    with pytest.raises(EDLError):
        to_edl(project)  # the title has no picture yet
    edl = to_edl(project, {title_clip.media_ref.title: Path("C:/cache/title.png")})
    title_line, photo_line = edl.splitlines()[-2:]

    # Escaped twice: as a filter option value, then as part of the graph.
    assert r"movie=filename=C\\:/cache/title.png," in title_line
    assert title_line.endswith(",start=0.000000,length=5.000000")
    assert (
        r"movie=filename=C\\:/My Photos\, 2026/it\\\'s \[one\]\; 100%.jpg"
        ",scale=1280:1280:force_original_aspect_ratio=decrease,"
        "loop=loop=-1:size=1,setpts=N*1001/30000/TB[out0];"
        "anullsrc=r=48000:cl=stereo[out1]"
    ) in photo_line
    assert photo_line.endswith(",start=0.000000,length=3.000000")


def test_generated_spans_are_the_gaps_and_pictures() -> None:
    video = make_media(Path("/v.mp4"))
    items = [
        Clip(media_ref=video, source_in=Fraction(0), source_out=Fraction(2)),
        Gap(duration=Fraction(1)),
        new_title_clip(),
        Clip(media_ref=video, source_in=Fraction(0), source_out=Fraction(1)),
    ]
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])
    assert generated_spans(project) == [(Fraction(2), Fraction(3)), (Fraction(3), Fraction(8))]


def test_photo_and_gap_play_seek_and_fade_live(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_video_clip
) -> None:
    """Through the real Player, for [video][photo (fades in)][gap][title]:
    the sound stays in sync through the photo; seeks into the photo and the
    gap land where asked, also backwards within them (where mpv alone gets
    stuck at the segment's end); and the photo's fade in animates."""
    import subprocess

    from PySide6.QtGui import QImage

    from penultimate_cut.preview.player import Player
    from penultimate_cut.ui.titles import title_images

    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache"))
    folder = tmp_path / "My Photos, [2026]"
    folder.mkdir()
    white = folder / "it's white; 100%.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=64x48",
         "-frames:v", "1", str(white)],
        check=True,
    )  # fmt: skip
    video = make_media(make_video_clip("edl_live.mp4", duration=2.0, fps=30, size="64x48"))
    photo = MediaRef(
        path=white,
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
        width=64,
        height=48,
    )
    items = [
        Clip(media_ref=video, source_in=Fraction(0), source_out=Fraction(2)),
        Clip(media_ref=photo, source_in=Fraction(0), source_out=Fraction(3), fade_in=True),
        Gap(duration=Fraction(2)),
        new_title_clip(Title(text="", background="#000000")),
    ]
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])
    edl_text = to_edl(project, title_images(project, 64, 48))
    spans = generated_spans(project)

    player = Player(keep_open="yes")
    try:
        player.load_edl(edl_text, generated=spans)
        assert player.wait_until_ready()
        player.seek(Fraction(1))
        player.play()
        assert _wait_for(lambda: (player.position or 0) > Fraction(4), timeout=8)
        audio = player._mpv.audio_pts
        assert audio is not None and abs(audio - float(player.position or 0)) < 0.25
        player.pause()
        for target in (Fraction(4), Fraction(5, 2), Fraction(6), Fraction(11, 2), Fraction(3), 0):
            player.seek(Fraction(target))
            assert _wait_for(lambda t=target: _near(player.position, t)), (target, player.position)
    finally:
        player.close()

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    player = Player(
        vo="image",
        vo_image_outdir=str(frames_dir),
        vo_image_format="png",
        untimed=True,
        keep_open="no",
        start="2",
        end="5",
    )
    try:
        player.set_filters(*preview_filters(project))
        player.load_edl(edl_text, start=Fraction(2))
        player._mpv.wait_for_playback()
    finally:
        player.close()
    frames = sorted(frames_dir.glob("*.png"))
    brightness = [QImage(str(f)).pixelColor(32, 24).red() for f in frames]
    # 3 s at 30 fps, give or take a frame at either end of the recording.
    assert 85 <= len(frames) <= 91
    assert brightness[0] < 30  # black as the photo starts fading in...
    assert any(90 < b < 170 for b in brightness[10:20])  # ...half way after 0.5 s...
    assert min(brightness[35:]) > 240  # ...then white


def _near(position: Fraction | None, target: Fraction) -> bool:
    """Within a frame: a seek into a gap or picture reloads, landing to the frame."""
    return position is not None and abs(position - target) <= Fraction(1, 30)


def _wait_for(condition, timeout: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False
