"""Project -> ffmpeg filtergraph + input args.

Builds everything needed to turn a Project into one concatenated, conformed
output stream: a list of input args (one entry per `-i`) and a
`-filter_complex` string that trims each clip to its exact in/out points,
conforms frame rate/resolution/pixel format, letterboxes mismatched aspect
ratios per the clip's Fit Mode, fills gaps with generated black video +
silence, and concatenates everything into a single `[outv]`/`[outa]` pair.
`encoders.py`/`runner.py` turn this plan into an actual ffmpeg invocation.

Scoped to one track, and any clip with a non-default speed raises
`FiltergraphError`: speed change (pitch-correct atempo chaining, etc.) is a
Phase 2 feature with enough real complexity to deserve its own pass, and its
default (speed=1) is a true no-op while it's unreachable from the UI. Fit
Mode is different: even its default ("black_bars") requires actively
letterboxing a mismatched clip, so it's implemented in full (all three
modes), since "do nothing" would just mean stretched, distorted video.

Per-clip volume/mute and fades are applied to each clip's own segment,
whose timestamps start at 0 after trimming, so a fade-out starts at
`duration - fade_length`. Clips at their defaults get no extra filters, so
their part of the graph is unchanged.

Clips are cut with the `trim`/`atrim` filters (exact frame/sample boundaries
on the fully decoded stream), not `-ss` before `-i` (fast, but only
keyframe-accurate) -- correctness over export speed. Revisit only if export
time on long sources actually becomes a real problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from penultimate_cut.model.project import Clip, FitMode, Gap, Project, auto_resolution, even
from penultimate_cut.model.time import format_seconds

ResolutionChoice = Literal["auto", "1080p", "720p", "480p", "360p"]

_RESOLUTION_SHORT_SIDES: dict[str, int] = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
""""720p" means 720 on the *short* side -- 1280x720 landscape, 720x1280
vertical -- the way phones and video sites use the term."""
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


def build_filtergraph(project: Project, resolution: ResolutionChoice = "auto") -> FFmpegPlan:
    """Build the input list and filter_complex for exporting `project`."""
    if len(project.tracks) != 1:
        raise FiltergraphError("export only supports a single track for now")
    track = project.tracks[0]
    if not track.items:
        raise FiltergraphError("nothing to export: the track is empty")

    width, height = target_resolution(project, resolution)
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
            f"setpts=PTS-STARTPTS,{_SQUARE_PIXELS}[v{index}pre]"
        )
    else:
        inputs.append(["-i", str(clip.media_ref.path)])
        pre = (
            f"[{input_index}:v]trim=start={format_seconds(start)}:end={format_seconds(end)},"
            f"setpts=PTS-STARTPTS,fps={rate},{_SQUARE_PIXELS}[v{index}pre]"
        )
    video_fades = _fades("fade", clip)
    fit_label = f"v{index}fit" if video_fades else f"v{index}"
    fit = _fit_stage(clip.fit_mode, width, height, f"v{index}pre", fit_label)
    video_filter = f"{pre};{fit}"
    if video_fades:
        video_filter += f";[{fit_label}]{','.join(video_fades)}[v{index}]"

    if clip.media_ref.has_audio:
        sound = [] if clip.gain == 1 else [f"volume={format_seconds(clip.gain)}"]
        sound += _fades("afade", clip)
        audio_filter = (
            f"[{input_index}:a]atrim=start={format_seconds(start)}:end={format_seconds(end)},"
            f"asetpts=PTS-STARTPTS,{','.join([_aformat(), *sound])}[a{index}]"
        )
    else:
        inputs.append(
            ["-f", "lavfi", "-i", f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo:d={duration_s}"]
        )
        filler_index = input_index + 1
        audio_filter = f"[{filler_index}:a]{_aformat()}[a{index}]"

    return inputs, video_filter, audio_filter


def _fades(filter_name: str, clip: Clip) -> list[str]:
    """`fade` (video) or `afade` (audio) filters for the clip's fade in from
    / out to black, timed within its own segment."""
    length = clip.fade_length
    fades = []
    if clip.fade_in:
        fades.append(f"{filter_name}=t=in:st=0:d={format_seconds(length)}")
    if clip.fade_out:
        fades.append(
            f"{filter_name}=t=out:st={format_seconds(clip.duration - length)}"
            f":d={format_seconds(length)}"
        )
    return fades


def _gap_filters(
    gap: Gap, index: int, input_index: int, width: int, height: int, rate: Fraction
) -> tuple[list[list[str]], str, str]:
    duration_s = format_seconds(gap.duration)
    video_input = ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={rate}:d={duration_s}"]
    audio_input = ["-f", "lavfi", "-i", f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo:d={duration_s}"]
    video_filter = f"[{input_index}:v]{_SEGMENT_FORMAT}[v{index}]"
    audio_filter = f"[{input_index + 1}:a]{_aformat()}[a{index}]"
    return [video_input, audio_input], video_filter, audio_filter


# concat requires every segment's SAR to match *exactly* -- it compares the
# raw ratio, so even an unreduced 832:832 (from some phone videos) fails
# against a gap's 1:1. Sources with non-square pixels (old camcorder/DV
# footage) are first stretched to their real display shape, so the fit
# stage's aspect math works on what the viewer actually sees; every segment
# then ends stamped 1:1.
_SQUARE_PIXELS = "scale=trunc(iw*sar/2)*2:ih,setsar=1"
_SEGMENT_FORMAT = "format=yuv420p,setsar=1"


def _aformat() -> str:
    return f"aformat=sample_rates={_OUTPUT_AUDIO_RATE}:channel_layouts={_OUTPUT_CHANNEL_LAYOUT}"


def _fit_stage(fit_mode: FitMode, width: int, height: int, in_label: str, out_label: str) -> str:
    if fit_mode == "black_bars":
        return (
            f"[{in_label}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,{_SEGMENT_FORMAT}[{out_label}]"
        )
    if fit_mode == "crop_to_fill":
        return (
            f"[{in_label}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},{_SEGMENT_FORMAT}[{out_label}]"
        )
    if fit_mode == "blurred_background":
        bg, fg, bgblur, fgfit = (
            f"{in_label}bg",
            f"{in_label}fg",
            f"{in_label}bgblur",
            f"{in_label}fgfit",
        )
        blur_w, blur_h = max(2, width // 4), max(2, height // 4)
        # boxblur rejects a radius that's half the (chroma) plane or more,
        # which a fixed 20 hits at small export sizes (e.g. 320x240).
        luma_radius = max(1, min(20, min(blur_w, blur_h) // 2 - 1))
        chroma_radius = max(1, min(luma_radius, min(blur_w, blur_h) // 4 - 1))
        return (
            f"[{in_label}]split=2[{bg}][{fg}];"
            f"[{bg}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},scale={blur_w}:{blur_h},"
            f"boxblur=lr={luma_radius}:lp=2:cr={chroma_radius}:cp=2,scale={width}:{height},"
            f"eq=brightness=-0.05[{bgblur}];"
            f"[{fg}]scale={width}:{height}:force_original_aspect_ratio=decrease[{fgfit}];"
            f"[{bgblur}][{fgfit}]overlay=(W-w)/2:(H-h)/2,{_SEGMENT_FORMAT}[{out_label}]"
        )
    raise FiltergraphError(f"unknown fit mode: {fit_mode}")


def target_resolution(project: Project, resolution: ResolutionChoice) -> tuple[int, int]:
    """The actual output width/height `build_filtergraph` would use for this
    resolution choice. Public (not just an internal helper) because the
    export dialog's live file-size estimate and its "Automatic (WxH)" label
    need it too, and unlike `build_filtergraph` it works on any project --
    including an empty one, via `auto_resolution`'s fallback -- rather than
    requiring one that's actually exportable."""
    width, height = auto_resolution(project)
    if resolution == "auto":
        return width, height

    target_short = _RESOLUTION_SHORT_SIDES[resolution]
    current_short = min(width, height)
    if target_short >= current_short:
        # Never upscale past the clips' own resolution.
        return width, height
    scale = Fraction(target_short, current_short)
    return even(width * scale), even(height * scale)
