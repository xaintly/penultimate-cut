import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.export.filtergraph import (
    FiltergraphError,
    build_filtergraph,
    target_resolution,
)
from penultimate_cut.media.probe import probe
from penultimate_cut.model.project import (
    Clip,
    Gap,
    MediaRef,
    Project,
    Title,
    Track,
    new_title_clip,
)


def make_media(
    path: Path, *, width: int = 320, height: int = 240, has_audio: bool = True
) -> MediaRef:
    return MediaRef(
        path=path,
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=has_audio,
        width=width,
        height=height,
    )


def render(tmp_path: Path, plan, name: str = "out.mp4") -> Path:
    """Assemble a full ffmpeg command from `plan` with minimal fixed encoder
    settings and run it for real -- exercising exactly the filtergraph/input
    args this module produces, not a hand-written equivalent."""
    output_path = tmp_path / name
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for input_arg in plan.input_args:
        args.extend(input_arg)
    args.extend(
        [
            "-filter_complex",
            plan.filter_complex,
            "-map",
            plan.video_label,
            "-map",
            plan.audio_label,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_path),
        ]
    )
    subprocess.run(args, check=True, capture_output=True, text=True)
    return output_path


# --- structural / fast tests -------------------------------------------------


def test_multiple_tracks_rejected(tmp_path: Path) -> None:
    clip = Clip(
        media_ref=make_media(tmp_path / "a.mp4"), source_in=Fraction(0), source_out=Fraction(2)
    )
    project = Project(
        frame_rate=Fraction(30),
        tracks=[Track(kind="video", items=[clip]), Track(kind="audio", items=[clip])],
    )
    with pytest.raises(FiltergraphError):
        build_filtergraph(project)


def test_empty_track_rejected() -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    with pytest.raises(FiltergraphError):
        build_filtergraph(project)


def test_target_resolution_works_on_an_empty_project() -> None:
    # Unlike build_filtergraph, target_resolution has to work on a project
    # with nothing in it yet -- the export dialog's live size estimate
    # calls it before the user has necessarily imported anything.
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[])])
    assert target_resolution(project, "auto") == (1280, 720)
    assert target_resolution(project, "720p") == (1280, 720)


def test_speed_changed_clip_rejected(tmp_path: Path) -> None:
    clip = Clip(
        media_ref=make_media(tmp_path / "a.mp4"),
        source_in=Fraction(0),
        source_out=Fraction(2),
        speed=Fraction(2),
    )
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    with pytest.raises(FiltergraphError):
        build_filtergraph(project)


@pytest.mark.parametrize(
    ("kwargs", "audio_tail"),
    [
        ({}, "aformat=sample_rates=48000:channel_layouts=stereo[a0]"),
        ({"volume": 50}, "channel_layouts=stereo,volume=0.500000[a0]"),
        ({"volume": 50, "muted": True}, "channel_layouts=stereo,volume=0.000000[a0]"),
        (
            {"fade_in": True, "fade_out": True},
            "stereo,afade=t=in:st=0:d=1.000000,afade=t=out:st=2.000000:d=1.000000[a0]",
        ),
    ],
)
def test_volume_mute_and_fades_filter_each_clip_segment(
    tmp_path: Path, kwargs: dict, audio_tail: str
) -> None:
    clip = Clip(
        media_ref=make_media(tmp_path / "a.mp4"),
        source_in=Fraction(0),
        source_out=Fraction(3),
        **kwargs,
    )
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    graph = build_filtergraph(project).filter_complex
    assert audio_tail in graph
    if kwargs.get("fade_in"):
        assert "[v0fit]fade=t=in:st=0:d=1.000000,fade=t=out:st=2.000000:d=1.000000[v0]" in graph
    else:
        assert "fade" not in graph


def test_fades_on_a_short_clip_share_its_length(tmp_path: Path) -> None:
    clip = Clip(
        media_ref=make_media(tmp_path / "a.mp4"),
        source_in=Fraction(0),
        source_out=Fraction(3, 2),
        fade_in=True,
        fade_out=True,
    )
    assert clip.fade_length == Fraction(3, 4)
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    graph = build_filtergraph(project).filter_complex
    assert "fade=t=in:st=0:d=0.750000,fade=t=out:st=0.750000:d=0.750000" in graph


def test_audio_only_media_on_video_track_rejected(tmp_path: Path) -> None:
    media = MediaRef(
        path=tmp_path / "a.m4a",
        relative_path=None,
        duration=Fraction(10),
        frame_rate=None,
        has_video=False,
        has_audio=True,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    with pytest.raises(FiltergraphError):
        build_filtergraph(project)


def test_still_image_clip_loops_input_and_starts_at_zero(tmp_path: Path) -> None:
    media = MediaRef(
        path=tmp_path / "photo.jpg",
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    # Reused still image with a nonzero source_in, which is meaningless for a
    # static image -- the export must still start=0 like the EDL preview does.
    clip = Clip(media_ref=media, source_in=Fraction(5), source_out=Fraction(8))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project)

    assert ["-loop", "1", "-i", str(media.path)] in plan.input_args
    assert "trim=start=0:end=3.000000" in plan.filter_complex


def test_clip_without_audio_gets_silent_filler_input(tmp_path: Path) -> None:
    media = make_media(tmp_path / "a.mp4", has_audio=False)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project)

    assert any("anullsrc" in " ".join(args) for args in plan.input_args)
    assert len(plan.input_args) == 2


def test_gap_produces_lavfi_color_and_anullsrc_with_exact_duration(tmp_path: Path) -> None:
    project = Project(
        frame_rate=Fraction(30), tracks=[Track(kind="video", items=[Gap(duration=Fraction("2.5"))])]
    )

    plan = build_filtergraph(project)

    flat = [" ".join(args) for args in plan.input_args]
    assert any("color=c=black" in a and "d=2.500000" in a for a in flat)
    assert any("anullsrc" in a and "d=2.500000" in a for a in flat)


@pytest.mark.parametrize(
    ("fit_mode", "expected_substring"),
    [
        ("black_bars", "pad=640:480"),
        ("crop_to_fill", "crop=640:480"),
        ("blurred_background", "boxblur=lr=20:lp=2:cr=20:cp=2"),
    ],
)
def test_fit_mode_produces_expected_filter_shape(
    tmp_path: Path, fit_mode: str, expected_substring: str
) -> None:
    media = make_media(tmp_path / "a.mp4", width=640, height=480)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2), fit_mode=fit_mode)
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project, resolution="auto")

    assert plan.width == 640 and plan.height == 480
    assert expected_substring in plan.filter_complex


def test_resolution_choice_scales_down_preserving_aspect(tmp_path: Path) -> None:
    media = make_media(tmp_path / "a.mp4", width=1920, height=1080)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project, resolution="720p")

    assert plan.height == 720
    assert plan.width == 1280


def test_resolution_choice_applies_to_the_short_side_of_vertical_video(tmp_path: Path) -> None:
    # "720p" for a phone video is 720x1280, not a tiny 405x720.
    media = make_media(tmp_path / "a.mp4", width=1080, height=1920)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    assert target_resolution(project, "720p") == (720, 1280)


def test_resolution_choice_never_upscales(tmp_path: Path) -> None:
    media = make_media(tmp_path / "a.mp4", width=640, height=480)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project, resolution="1080p")

    assert (plan.width, plan.height) == (640, 480)


# --- real ffmpeg + ffprobe integration (per CLAUDE.md testing guidance) -----


def test_export_single_clip_has_correct_duration_and_resolution(
    tmp_path: Path, make_video_clip
) -> None:
    source = make_video_clip("filt_a.mp4", duration=3.0, fps=30, size="320x240")
    media = make_media(source)
    clip = Clip(media_ref=media, source_in=Fraction(0.5), source_out=Fraction(2.5))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project)
    output_path = render(tmp_path, plan)
    info = probe(output_path)

    assert info.has_video and info.has_audio
    assert info.width == 320 and info.height == 240
    assert abs(info.duration - Fraction(2)) < Fraction(1, 15)


def _brightness_at(path: Path, seconds: float) -> int:
    """Average brightness (0-255) of the frame at `seconds`."""
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", str(seconds), "-i", str(path),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    )  # fmt: skip
    return result.stdout[0]


def _loudness(path: Path, start: float, length: float) -> float:
    """Mean volume in dB over `[start, start + length)`; about -91 is silence."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(length), "-i", str(path),
         "-vn", "-af", "volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    line = next(x for x in result.stderr.splitlines() if "mean_volume" in x)
    return float(line.split("mean_volume:")[1].split("dB")[0])


def test_export_fades_and_mutes_for_real(tmp_path: Path, make_video_clip) -> None:
    source = make_video_clip("filt_fade.mp4", duration=3.0, fps=30, size="320x240")
    media = make_media(source)
    faded = Clip(
        media_ref=media, source_in=Fraction(0), source_out=Fraction(3), fade_in=True, fade_out=True
    )
    muted = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2), muted=True)
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[faded, muted])])

    output_path = render(tmp_path, build_filtergraph(project))

    assert _brightness_at(output_path, 0.0) < 20  # black at the start of the fade in
    assert _brightness_at(output_path, 1.5) > 60  # full picture between the fades
    assert _brightness_at(output_path, 2.9) < 20  # nearly black near the end of the fade out
    assert _loudness(output_path, 1.2, 0.6) > -40  # the faded clip's sound, mid-clip
    assert _loudness(output_path, 3.2, 1.5) < -80  # the muted clip


def test_export_clip_and_gap_total_duration(tmp_path: Path, make_video_clip) -> None:
    source = make_video_clip("filt_b.mp4", duration=3.0, fps=30, size="320x240")
    media = make_media(source)
    clip1 = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(1))
    clip2 = Clip(media_ref=media, source_in=Fraction(1), source_out=Fraction(3))
    track = Track(kind="video", items=[clip1, Gap(duration=Fraction(1)), clip2])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    plan = build_filtergraph(project)
    output_path = render(tmp_path, plan)
    info = probe(output_path)

    assert abs(info.duration - Fraction(4)) < Fraction(1, 15)


def test_export_still_image_clip(tmp_path: Path, make_still_image) -> None:
    image_path = make_still_image("filt_still.png", size="320x240")
    media = MediaRef(
        path=image_path,
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project)
    output_path = render(tmp_path, plan)
    info = probe(output_path)

    assert info.has_video and info.has_audio  # silent filler audio track
    assert abs(info.duration - Fraction(2)) < Fraction(1, 15)


def test_export_mismatched_aspect_ratio_conforms_to_target_resolution(
    tmp_path: Path, make_video_clip
) -> None:
    portrait_source = make_video_clip("filt_portrait.mp4", duration=2.0, fps=30, size="240x320")
    media = make_media(portrait_source, width=240, height=320)
    clip = Clip(
        media_ref=media, source_in=Fraction(0), source_out=Fraction(2), fit_mode="black_bars"
    )
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project)
    output_path = render(tmp_path, plan)
    info = probe(output_path)

    assert (info.width, info.height) == (240, 320)


@pytest.mark.parametrize("fit_mode", ["black_bars", "crop_to_fill", "blurred_background"])
def test_export_joins_non_square_pixel_clip_with_gap_and_square_clip(
    tmp_path: Path, make_video_clip, fit_mode: str
) -> None:
    """Regression: concat needs identical SAR on every segment, so a clip
    with non-square pixels (old camcorder footage; some phones write odd
    ratios like 832:832) next to a gap or a normal clip failed to export.
    Also at a small size, where blurred background's blur radius used to be
    too big for boxblur."""
    anamorphic = tmp_path / "anamorphic.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=1:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=duration=1",
            "-vf", "setsar=4/3", "-c:v", "libx264", "-c:a", "aac", "-shortest",
            str(anamorphic),
        ],
        check=True,
    )  # fmt: skip
    square = make_video_clip("filt_square_sar.mp4", duration=1.0, fps=30, size="320x240")
    items = [
        Clip(
            media_ref=make_media(path),
            source_in=Fraction(0),
            source_out=Fraction(1),
            fit_mode=fit_mode,
        )
        for path in (anamorphic, square)
    ]
    project = Project(
        frame_rate=Fraction(30),
        tracks=[Track(kind="video", items=[items[0], Gap(duration=Fraction(1)), items[1]])],
    )

    output_path = render(tmp_path, build_filtergraph(project))
    sar = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=sample_aspect_ratio", "-of", "csv=p=0", str(output_path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert sar == "1:1"
    assert abs(probe(output_path).duration - 3) < Fraction(1, 10)


def test_title_is_exported_from_its_drawn_picture(
    tmp_path: Path, make_video_clip, monkeypatch: pytest.MonkeyPatch
) -> None:
    from penultimate_cut.ui.titles import title_images

    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache"))
    source = make_video_clip("filt_title.mp4", duration=2.0, fps=30, size="320x240")
    title = Title(text="Hello", background="#ffffff", text_color="#ffffff")
    clip = Clip(media_ref=make_media(source), source_in=Fraction(0), source_out=Fraction(2))
    track = Track(kind="video", items=[replace(new_title_clip(title), fade_out=False), clip])
    project = Project(frame_rate=Fraction(30), tracks=[track])

    with pytest.raises(FiltergraphError):
        build_filtergraph(project)  # not drawn yet
    images = title_images(project, *target_resolution(project, "auto"))
    plan = build_filtergraph(project, "auto", images)
    assert ["-loop", "1", "-i", str(images[title])] in plan.input_args

    output_path = render(tmp_path, plan)
    info = probe(output_path)
    assert abs(info.duration - Fraction(7)) < Fraction(1, 15)
    assert _brightness_at(output_path, 0.1) < 60  # fading in from black
    assert _brightness_at(output_path, 3.0) > 200  # the (white) card itself
