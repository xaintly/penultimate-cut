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
- A still image can only be held from `start=0`; any nonzero `start` makes
  mpv fail to open it (unlike a real gap's lavfi source, which can start
  anywhere since it's infinite). `start` is forced to 0 for still-image
  clips here regardless of the clip's own `source_in`.

EDL has no per-segment filters, so clip volume/mute and fades are previewed
by `preview_filters`: one video and one audio filter chain for the whole
timeline, each filter switched on (`enable=`) only during its clip's span.
Checked against a live libmpv: the filters see timeline time (a fade on the
second clip lands at its start, also when playback starts mid-fade); audio
switches within about 20 ms of a clip boundary, since it's filtered in
chunks. Export applies the same settings per clip, exactly.

Title cards are drawn beforehand (by `ui/titles.py`, at the preview's
size) and play as an image list (`mf://@list`) repeating the picture at
`TITLE_FPS`: `to_edl` takes the list for each title rather than drawing
anything itself. Not as a plain still image: mpv shows that as one frame at
its start, so an exact seek into the middle skips past it (to the next
clip) and a fade has no frames to animate. Nor as a looping lavfi `movie`
source: after a seek backwards within it, every later seek into it lands
at its end. Both checked against a live libmpv; the image list seeks to
within a frame from anywhere, both ways, and plays in real time.

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

TITLE_FPS = 10
"""Frames per second of a title card's image list: the player's `mf-fps`."""

_GAP_AUDIO_RATE = 48000


class EDLError(ValueError):
    """`project` can't be represented as an EDL (yet)."""


def to_edl(project: Project, title_lists: Mapping[Title, Path] | None = None) -> str:
    """Render `project` as mpv EDL text. Empty tracks are omitted.
    `title_lists` holds the image list for each title card on the timeline
    (see `model.project.project_titles`), long enough for its longest use."""
    width, height = auto_resolution(project)
    lists = title_lists or {}
    lines = [HEADER]
    for track in project.tracks:
        if not track.items:
            continue
        lines.append("!new_stream")
        for item in track.items:
            lines.append(_item_line(item, track.kind, project.frame_rate, width, height, lists))
    return "\n".join(lines) + "\n"


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
    title_lists: Mapping[Title, Path],
) -> str:
    if isinstance(item, Gap):
        return _gap_line(item, track_kind, project_rate, width, height)
    return _clip_line(item, title_lists)


def _clip_line(clip: Clip, title_lists: Mapping[Title, Path]) -> str:
    if clip.speed != 1:
        raise EDLError("speed-changed clips can't be previewed via EDL yet")
    start = Fraction(0) if clip.media_ref.is_still_image else clip.source_in
    length = clip.source_out - clip.source_in
    return _segment(_source(clip, title_lists), start, length)


def _source(clip: Clip, title_lists: Mapping[Title, Path]) -> str:
    title = clip.media_ref.title
    if title is None:
        return str(clip.media_ref.path)
    if title not in title_lists:
        raise EDLError("a title card hasn't been drawn yet")
    return f"mf://@{title_lists[title]}"


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


def _segment(file: str, start: Fraction, length: Fraction) -> str:
    return f"file={_escape(file)},start={format_seconds(start)},length={format_seconds(length)}"


def _escape(value: str) -> str:
    """Length-prefixed EDL value form: safe for any content, incl. `,;\\n!=`."""
    byte_length = len(value.encode("utf-8"))
    return f"%{byte_length}%{value}"
