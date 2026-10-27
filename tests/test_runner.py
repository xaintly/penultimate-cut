from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.export.encoders import encoder_args
from penultimate_cut.export.filtergraph import build_filtergraph
from penultimate_cut.export.runner import (
    ExportCancelled,
    ExportProgress,
    RunnerError,
    _parse_progress,
    run_export,
)
from penultimate_cut.media.probe import probe
from penultimate_cut.model.project import Clip, MediaRef, Project, Track


def lavfi_args(duration: float) -> list[str]:
    return [
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}:size=320x240:rate=30",
        "-f",
        "lavfi",
        "-i",
        f"sine=duration={duration}",
    ]


def test_successful_export_reports_progress_and_produces_valid_file(tmp_path: Path) -> None:
    output_path = tmp_path / "out.mp4"
    args = [
        *lavfi_args(2.0),
        *encoder_args("libx264", "small_file", 320, 240, Fraction(30)),
    ]
    progress_calls: list[ExportProgress] = []

    run_export(args, output_path, Fraction(2), on_progress=progress_calls.append)

    assert output_path.exists()
    assert len(progress_calls) > 0
    assert all(0.0 <= p.fraction <= 1.0 for p in progress_calls)
    assert progress_calls[-1].fraction > 0.5

    info = probe(output_path)
    assert info.has_video and info.has_audio
    assert abs(info.duration - Fraction(2)) < Fraction(1, 5)


def test_cancel_terminates_ffmpeg_and_deletes_partial_file(tmp_path: Path) -> None:
    output_path = tmp_path / "out.mp4"
    args = [
        *lavfi_args(30.0),  # long enough to guarantee a progress callback before completion
        *encoder_args("libx264", "small_file", 320, 240, Fraction(30)),
    ]

    with pytest.raises(ExportCancelled):
        run_export(args, output_path, Fraction(30), cancel_check=lambda: True)

    assert not output_path.exists()


def test_cancel_after_some_progress(tmp_path: Path) -> None:
    output_path = tmp_path / "out.mp4"
    args = [
        # Long enough that a fast machine can't finish before the second
        # progress report; it's cancelled right then, so it costs no time.
        *lavfi_args(600.0),
        *encoder_args("libx264", "small_file", 320, 240, Fraction(30)),
    ]
    calls = {"n": 0}

    def cancel_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(ExportCancelled):
        run_export(args, output_path, Fraction(30), cancel_check=cancel_after_first)

    assert calls["n"] >= 2
    assert not output_path.exists()


def test_ffmpeg_failure_raises_runner_error_with_stderr(tmp_path: Path) -> None:
    output_path = tmp_path / "out.mp4"
    args = ["-i", str(tmp_path / "does_not_exist.mp4")]

    with pytest.raises(RunnerError) as exc_info:
        run_export(args, output_path, Fraction(1))

    assert not output_path.exists()
    assert str(exc_info.value)


def test_full_pipeline_project_to_encoded_file(tmp_path: Path, make_video_clip) -> None:
    source = make_video_clip("runner_pipeline.mp4", duration=2.0, fps=30, size="320x240")
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
    clip = Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=[clip])])

    plan = build_filtergraph(project)
    args = []
    for input_arg in plan.input_args:
        args.extend(input_arg)
    args.extend(
        ["-filter_complex", plan.filter_complex, "-map", plan.video_label, "-map", plan.audio_label]
    )
    args.extend(encoder_args("libx264", "balanced", plan.width, plan.height, plan.frame_rate))

    output_path = tmp_path / "final.mp4"
    run_export(args, output_path, project.duration)

    info = probe(output_path)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (320, 240)
    assert abs(info.duration - Fraction(2)) < Fraction(1, 5)


def test_progress_block_without_a_time_is_skipped() -> None:
    # Real ffmpeg output: with several clips and gaps it reports N/A
    # mid-export, which used to crash the export thread.
    block = {"out_time_us": "N/A", "speed": "N/A", "progress": "continue"}
    assert _parse_progress(block, Fraction(10)) is None
    assert _parse_progress({"progress": "continue"}, Fraction(10)) is None
    parsed = _parse_progress({"out_time_us": "2500000", "speed": "2.0x"}, Fraction(10))
    assert parsed == ExportProgress(fraction=0.25, out_time=Fraction(5, 2), speed=2.0)


def test_unexpected_error_stops_ffmpeg_and_deletes_partial_file(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"

    def explode(_progress: ExportProgress) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_export(
            [*lavfi_args(30.0), *encoder_args("libx264", "balanced", 320, 240, Fraction(30))],
            output,
            Fraction(30),
            on_progress=explode,
        )
    assert not output.exists()
