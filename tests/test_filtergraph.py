import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.export.filtergraph import FiltergraphError, build_filtergraph
from penultimate_cut.media.probe import probe
from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Track


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


@pytest.mark.parametrize("kwargs", [{"volume_db": -6.0}, {"muted": True}])
def test_nondefault_volume_or_mute_rejected(tmp_path: Path, kwargs: dict) -> None:
    clip = Clip(
        media_ref=make_media(tmp_path / "a.mp4"),
        source_in=Fraction(0),
        source_out=Fraction(2),
        **kwargs,
    )
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])
    with pytest.raises(FiltergraphError):
        build_filtergraph(project)


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
        ("blurred_background", "boxblur=20:2"),
    ],
)
def test_fit_mode_produces_expected_filter_shape(
    tmp_path: Path, fit_mode: str, expected_substring: str
) -> None:
    media = make_media(tmp_path / "a.mp4", width=640, height=480)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2), fit_mode=fit_mode)
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project, resolution="project")

    assert plan.width == 640 and plan.height == 480
    assert expected_substring in plan.filter_complex


def test_resolution_choice_scales_down_preserving_aspect(tmp_path: Path) -> None:
    media = make_media(tmp_path / "a.mp4", width=1920, height=1080)
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project, resolution="720p")

    assert plan.height == 720
    assert plan.width == 1280


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
