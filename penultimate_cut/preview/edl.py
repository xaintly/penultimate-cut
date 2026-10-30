"""Project -> mpv EDL text.

Lets mpv play the whole cut list as one seamless virtual stream without
rendering anything first: https://mpv.io/manual/master/#edl-files

Gaps have no native "blank segment" directive in EDL, so they're filled
with a generated black-video + silent-audio stream via mpv/ffmpeg's lavfi
input device (`av://lavfi:...`). Several details here were verified against
a live libmpv instance rather than just the spec, since they're easy to get
subtly wrong:

- Loading an EDL with protocol URLs (like `av://`) in its segments requires
  `load_unsafe_playlists=True` on the player, or mpv rejects the whole file
  as an "unsafe playlist" entry.
- An unnamed segment parameter containing a raw `=` (as any lavfi filter
  string does, e.g. `color=c=black`) gets misparsed as a `name=value` pair
  using everything before the *first* `=` as the name. Segments are written
  with explicit `file=`/`start=`/`length=` names to avoid this, and `file`
  values are always length-prefix-escaped (`%N%...`) so stray `,`/`;` in a
  real path or filter string can never be misread as EDL delimiters.
- The lavfi device needs its output pads explicitly named `[out0]`, `[out1]`
  (not arbitrary names) when a graph has more than one unconnected output.

Pictures (photos, and title cards drawn beforehand by `ui/titles.py`) are
generated the same way: the picture, shrunk once to the preview's size,
repeated at the project's frame rate, plus silence. Not as the plain image
file: mpv shows that as one frame, so an exact seek into its middle skips
to the next clip and a fade has no frames to animate; and with no sound of
its own, mpv plays the next clip's sound early, out of sync. Not as an mpv
image list (`mf://`) either, which seeks well but has no sound. All checked
against a live libmpv, as was that the generated picture keeps a phone
photo's EXIF rotation and that the escaping below survives file names full
of `,;[]'%` and spaces.

Generated segments have one weakness: mpv can't seek backwards within one
(it ends up at the segment's end, and stays stuck there). `generated_spans`
tells the player where they are, so it can reload at the target instead
(see `Player.seek`).

EDL has no per-segment filters, so clip volume/mute and fades are previewed
by `preview_filters`: one video and one audio filter chain for the whole
timeline, each filter switched on (`enable=`) only during its clip's span.
Checked against a live libmpv: the filters see timeline time (a fade on the
second clip lands at its start, also when playback starts mid-fade); audio
switches within about 20 ms of a clip boundary, since it's filtered in
chunks. Export applies the same settings per clip, exactly.

EDL timestamps are plain floating-point seconds -- there's no way to carry
our exact `Fraction` times through this format. That's fine for preview
scrubbing; export still cuts on exact frame boundaries via the ffmpeg
filtergraph, which is the thing that actually has to be frame-accurate.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path

from penultimate_cut.model.project import (
    Clip,
    Gap,
    Project,
    Title,
    TrackItem,
    TrackKind,
    auto_resolution,
)
from penultimate_cut.model.time import format_seconds

HEADER = "# mpv EDL v0"

_GAP_AUDIO_RATE = 48000

Span = tuple[Fraction, Fraction]


class EDLError(ValueError):
    """`project` can't be represented as an EDL (yet)."""


def to_edl(project: Project, title_pictures: Mapping[Title, Path] | None = None) -> str:
    """Render `project` as mpv EDL text. Empty tracks are omitted.
    `title_pictures` holds the drawn picture of each title card on the
    timeline (see `model.project.project_titles`)."""
    width, height = auto_resolution(project)
    pictures = title_pictures or {}
    lines = [HEADER]
    for track in project.tracks:
        if not track.items:
            continue
        lines.append("!new_stream")
        for item in track.items:
            lines.append(_item_line(item, track.kind, project.frame_rate, width, height, pictures))
    return "\n".join(lines) + "\n"


def generated_spans(project: Project) -> list[Span]:
    """(start, end) timeline spans played from generated segments -- gaps
    and pictures -- which mpv can't seek backwards within."""
    spans = []
    for track in project.tracks:
        start = Fraction(0)
        for item in track.items:
            end = start + item.duration
            if isinstance(item, Gap) or item.media_ref.is_still_image:
                spans.append((start, end))
            start = end
    return spans


def preview_filters(project: Project) -> tuple[str, str]:
    """(video, audio) lavfi filter chains for the clips' fades, volume, and
    mute, in timeline time; "" when there's nothing to apply."""
    video: list[str] = []
    audio: list[str] = []
    for track in project.tracks:
        start = Fraction(0)
        for item in track.items:
            if isinstance(item, Clip):
                _add_clip_filters(item, start, video, audio)
            start += item.duration
    return ",".join(video), ",".join(audio)


def _add_clip_filters(clip: Clip, start: Fraction, video: list[str], audio: list[str]) -> None:
    end = start + clip.duration
    has_audio = clip.media_ref.has_audio
    if has_audio and clip.gain != 1:
        audio.append(f"volume=volume={format_seconds(clip.gain)}:{_during(start, end)}")
    length = clip.fade_length
    fades = []
    if clip.fade_in:
        fades.append(("in", start))
    if clip.fade_out:
        fades.append(("out", end - length))
    for direction, fade_start in fades:
        timing = f"t={direction}:st={format_seconds(fade_start)}:d={format_seconds(length)}"
        span = _during(fade_start, fade_start + length)
        video.append(f"fade={timing}:{span}")
        if has_audio:
            audio.append(f"afade={timing}:{span}")


def _during(start: Fraction, end: Fraction) -> str:
    return f"enable='between(t,{format_seconds(start)},{format_seconds(end)})'"


def _item_line(
    item: TrackItem,
    track_kind: TrackKind,
    project_rate: Fraction,
    width: int,
    height: int,
    title_pictures: Mapping[Title, Path],
) -> str:
    if isinstance(item, Gap):
        return _gap_line(item, track_kind, project_rate, width, height)
    if item.speed != 1:
        raise EDLError("speed-changed clips can't be previewed via EDL yet")
    media = item.media_ref
    if media.title is not None:
        if media.title not in title_pictures:
            raise EDLError("a title card hasn't been drawn yet")
        return _picture_line(title_pictures[media.title], item, project_rate, width, height)
    if media.is_still_image:
        return _picture_line(media.path, item, project_rate, width, height)
    return _segment(str(media.path), item.source_in, item.source_out - item.source_in)


def _picture_line(picture: Path, clip: Clip, rate: Fraction, width: int, height: int) -> str:
    # Shrunk to fit a square of the preview's long side -- before any EXIF
    # rotation, which mpv applies afterwards -- once, before it's repeated.
    side = max(width, height)
    movie = _graph_escape(f"movie=filename={_option_escape(picture.as_posix())}")
    graph = (
        f"{movie},scale={side}:{side}:force_original_aspect_ratio=decrease,"
        f"loop=loop=-1:size=1,setpts=N*{rate.denominator}/{rate.numerator}/TB[out0];"
        f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo[out1]"
    )
    return _segment(f"av://lavfi:{graph}", Fraction(0), clip.duration)


def _gap_line(
    gap: Gap, track_kind: TrackKind, project_rate: Fraction, width: int, height: int
) -> str:
    if track_kind == "video":
        graph = (
            f"color=c=black:s={width}x{height}:r={project_rate}[out0];"
            f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo[out1]"
        )
    else:
        graph = f"anullsrc=r={_GAP_AUDIO_RATE}:cl=stereo[out0]"
    return _segment(f"av://lavfi:{graph}", Fraction(0), gap.duration)


def _option_escape(value: str) -> str:
    """A filter option's value: `\\`, `:` and `'` backslash-escaped."""
    return "".join("\\" + char if char in "\\:'" else char for char in value)


def _graph_escape(value: str) -> str:
    """A whole filter, within a filter graph: `\\'[],;` backslash-escaped."""
    return "".join("\\" + char if char in "\\'[],;" else char for char in value)


def _segment(file: str, start: Fraction, length: Fraction) -> str:
    return f"file={_escape(file)},start={format_seconds(start)},length={format_seconds(length)}"


def _escape(value: str) -> str:
    """Length-prefixed EDL value form: safe for any content, incl. `,;\\n!=`."""
    byte_length = len(value.encode("utf-8"))
    return f"%{byte_length}%{value}"
