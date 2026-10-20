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

EDL timestamps are plain floating-point seconds -- there's no way to carry
our exact `Fraction` times through this format. That's fine for preview
scrubbing; export still cuts on exact frame boundaries via the ffmpeg
filtergraph, which is the thing that actually has to be frame-accurate.
"""

from __future__ import annotations

from fractions import Fraction

from penultimate_cut.model.project import (
    Clip,
    Gap,
    Project,
    TrackItem,
    TrackKind,
    reference_resolution,
)
from penultimate_cut.model.time import format_seconds

HEADER = "# mpv EDL v0"

_GAP_AUDIO_RATE = 48000


class EDLError(ValueError):
    """`project` can't be represented as an EDL (yet)."""


def to_edl(project: Project) -> str:
    """Render `project` as mpv EDL text. Empty tracks are omitted."""
    width, height = reference_resolution(project)
    lines = [HEADER]
    for track in project.tracks:
        if not track.items:
            continue
        lines.append("!new_stream")
        for item in track.items:
            lines.append(_item_line(item, track.kind, project.frame_rate, width, height))
    return "\n".join(lines) + "\n"


def _item_line(
    item: TrackItem, track_kind: TrackKind, project_rate: Fraction, width: int, height: int
) -> str:
    if isinstance(item, Gap):
        return _gap_line(item, track_kind, project_rate, width, height)
    return _clip_line(item)


def _clip_line(clip: Clip) -> str:
    if clip.speed != 1:
        raise EDLError("speed-changed clips can't be previewed via EDL yet")
    start = Fraction(0) if clip.media_ref.is_still_image else clip.source_in
    length = clip.source_out - clip.source_in
    return _segment(str(clip.media_ref.path), start, length)


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
