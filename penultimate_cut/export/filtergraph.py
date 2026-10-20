"""Project -> ffmpeg filtergraph + input args.

Builds everything needed to turn a Project into one concatenated, conformed
output stream: a list of input args (one entry per `-i`) and a
`-filter_complex` string that trims each clip to its exact in/out points,
conforms frame rate/resolution/pixel format, letterboxes mismatched aspect
ratios per the clip's Fit Mode, fills gaps with generated black video +
silence, and concatenates everything into a single `[outv]`/`[outa]` pair.
`encoders.py`/`runner.py` turn this plan into an actual ffmpeg invocation.

Scoped to Phase 1: exactly one track, and any clip with a non-default speed
or volume/mute setting raises `FiltergraphError`. Those are Phase 2 features
(pitch-correct atempo chaining, etc.) with enough real complexity to deserve
their own pass rather than a half-built stand-in here -- their *default*
values (speed=1, volume_db=0, muted=False) are true no-ops, so skipping them
for now costs nothing while they're unreachable from the UI. Fit Mode is
different: even its default ("black_bars") requires actively letterboxing a
mismatched clip, so it's implemented in full (all three modes) rather than
deferred, since "do nothing" would just mean stretched, distorted video.

Clips are cut with the `trim`/`atrim` filters (exact frame/sample boundaries
on the fully decoded stream), not `-ss` before `-i` (fast, but only
keyframe-accurate) -- correctness over export speed. Revisit only if export
time on long sources actually becomes a real problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from penultimate_cut.model.project import Clip, FitMode, Gap, Project, reference_resolution
from penultimate_cut.model.time import format_seconds

ResolutionChoice = Literal["project", "1080p", "720p", "480p", "360p"]

_RESOLUTION_HEIGHTS: dict[str, int] = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
_GAP_AUDIO_RATE = 48000
_OUTPUT_AUDIO_RATE = 48000
_OUTPUT_CHANNEL_LAYOUT = "stereo"


class FiltergraphError(ValueError):
    """`project` can't be exported (yet) -- see module docstring for what's deferred."""


@dataclass
class FFmpegPlan:
    input_args: list[list[str]]
    """One entry per `-i`, e.g. `["-i", "/path.mp4"]` or `["-loop", "1", "-i", "/img.png"]`."""
    filter_complex: str
    video_label: str
    audio_label: str
    width: int
    height: int
    frame_rate: Fraction


def build_filtergraph(project: Project, resolution: ResolutionChoice = "project") -> FFmpegPlan:
    """Build the input list and filter_complex for exporting `project`."""
    if len(project.tracks) != 1:
        raise FiltergraphError("export only supports a single track for now")
    track = project.tracks[0]
    if not track.items:
        raise FiltergraphError("nothing to export: the track is empty")

    width, height = _target_resolution(project, resolution)
    rate = project.frame_rate

    input_args: list[list[str]] = []
    video_filters: list[str] = []
    audio_filters: list[str] = []

    for index, item in enumerate(track.items):
        if isinstance(item, Gap):
            new_inputs, v_filter, a_filter = _gap_filters(
                item, index, len(input_args), width, height, rate
            )
        else:
            new_inputs, v_filter, a_filter = _clip_filters(
                item, index, len(input_args), width, height, rate
            )
        input_args.extend(new_inputs)
        video_filters.append(v_filter)
        audio_filters.append(a_filter)

    n = len(track.items)
    video_concat = "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
    audio_concat = "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
    filter_complex = ";".join([*video_filters, *audio_filters, video_concat, audio_concat])

    return FFmpegPlan(
        input_args=input_args,
        filter_complex=filter_complex,
        video_label="[outv]",
        audio_label="[outa]",
        width=width,
        height=height,
        frame_rate=rate,
    )


# --- per-item filter construction -------------------------------------------


def _clip_filters(
    clip: Clip, index: int, input_index: int, width: int, height: int, rate: Fraction
) -> tuple[list[list[str]], str, str]:
    if clip.speed != 1:
        raise FiltergraphError("speed-changed clips can't be exported yet")
    if clip.volume_db != 0.0 or clip.muted:
        raise FiltergraphError("per-clip volume/mute can't be exported yet")
    if not clip.media_ref.has_video:
        raise FiltergraphError("a non-video clip can't sit on the video track")

    is_still = clip.media_ref.is_still_image
    start = Fraction(0) if is_still else clip.source_in
    end = clip.duration if is_still else clip.source_out
    duration_s = format_seconds(clip.duration)

    inputs: list[list[str]] = []
    if is_still:
        inputs.append(["-loop", "1", "-i", str(clip.media_ref.path)])
        pre = (
            f"[{input_index}:v]fps={rate},trim=start=0:end={duration_s},"
            f"setpts=PTS-STARTPTS[v{index}pre]"
        )
    else:
        inputs.append(["-i", str(clip.media_ref.path)])
        pre = (
            f"[{input_index}:v]trim=start={format_seconds(start)}:end={format_seconds(end)},"
            f"setpts=PTS-STARTPTS,fps={rate}[v{index}pre]"
        )
    fit = _fit_stage(clip.fit_mode, width, height, f"v{index}pre", f"v{index}")
    video_filter = f"{pre};{fit}"

    if clip.media_ref.has_audio:
        audio_filter = (
            f"[{input_index}:a]atrim=start={format_seconds(start)}:end={format_seconds(end)},"
            f"asetpts=PTS-STARTPTS,{_aformat()}[a{index}]"
        )
    else:
        inputs.append(
            ["-f", "lavfi", "-i", f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo:d={duration_s}"]
        )
        filler_index = input_index + 1
        audio_filter = f"[{filler_index}:a]{_aformat()}[a{index}]"

    return inputs, video_filter, audio_filter


def _gap_filters(
    gap: Gap, index: int, input_index: int, width: int, height: int, rate: Fraction
) -> tuple[list[list[str]], str, str]:
    duration_s = format_seconds(gap.duration)
    video_input = ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={rate}:d={duration_s}"]
    audio_input = ["-f", "lavfi", "-i", f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo:d={duration_s}"]
    video_filter = f"[{input_index}:v]format=yuv420p[v{index}]"
    audio_filter = f"[{input_index + 1}:a]{_aformat()}[a{index}]"
    return [video_input, audio_input], video_filter, audio_filter


def _aformat() -> str:
    return f"aformat=sample_rates={_OUTPUT_AUDIO_RATE}:channel_layouts={_OUTPUT_CHANNEL_LAYOUT}"


def _fit_stage(fit_mode: FitMode, width: int, height: int, in_label: str, out_label: str) -> str:
    if fit_mode == "black_bars":
        return (
            f"[{in_label}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p[{out_label}]"
        )
    if fit_mode == "crop_to_fill":
        return (
            f"[{in_label}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},format=yuv420p[{out_label}]"
        )
    if fit_mode == "blurred_background":
        bg, fg, bgblur, fgfit = (
            f"{in_label}bg",
            f"{in_label}fg",
            f"{in_label}bgblur",
            f"{in_label}fgfit",
        )
        blur_w, blur_h = max(2, width // 4), max(2, height // 4)
        return (
            f"[{in_label}]split=2[{bg}][{fg}];"
            f"[{bg}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},scale={blur_w}:{blur_h},boxblur=20:2,scale={width}:{height},"
            f"eq=brightness=-0.05[{bgblur}];"
            f"[{fg}]scale={width}:{height}:force_original_aspect_ratio=decrease[{fgfit}];"
            f"[{bgblur}][{fgfit}]overlay=(W-w)/2:(H-h)/2,format=yuv420p[{out_label}]"
        )
    raise FiltergraphError(f"unknown fit mode: {fit_mode}")


def _target_resolution(project: Project, resolution: ResolutionChoice) -> tuple[int, int]:
    ref_width, ref_height = reference_resolution(project)
    if resolution == "project":
        return ref_width, ref_height

    target_height = _RESOLUTION_HEIGHTS[resolution]
    if target_height >= ref_height:
        # Never upscale past the source's own resolution.
        return ref_width, ref_height

    aspect = Fraction(ref_width, ref_height)
    width = round(target_height * aspect)
    width += width % 2
    height = target_height + target_height % 2
    return width, height
