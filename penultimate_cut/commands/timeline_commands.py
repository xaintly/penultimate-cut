"""QUndoCommand wrappers around model/timeline_ops.py.

Each command captures the track's state before and after a single
timeline_ops call and swaps between the two on redo()/undo() -- cheap, since
a Track is just a dataclass holding a short list of items, and it means
undo/redo never has to re-run (or invert) the underlying operation.

Validation happens eagerly in __init__ by calling the timeline_ops function
right away: a command that would raise (e.g. trimming past the end of the
source) fails before it's ever pushed onto the undo stack, instead of
leaving a bad entry there. UI code should never mutate `Project.tracks`
directly -- always go through one of these via `undo_stack.push(...)`.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from PySide6.QtGui import QUndoCommand

from penultimate_cut.model import timeline_ops
from penultimate_cut.model.project import FitMode, Gap, Project, Track, TrackItem


class _TrackSwapCommand(QUndoCommand):
    """Base for commands that replace `project.tracks[track_index]` wholesale."""

    def __init__(self, text: str, project: Project, track_index: int, new_track: Track) -> None:
        super().__init__(text)
        self._project = project
        self._track_index = track_index
        self._old_track = project.tracks[track_index]
        self._new_track = new_track

    def redo(self) -> None:
        self._project.tracks[self._track_index] = self._new_track

    def undo(self) -> None:
        self._project.tracks[self._track_index] = self._old_track


class SplitCommand(_TrackSwapCommand):
    """Split the clip under the playhead at `position`."""

    def __init__(self, project: Project, track_index: int, position: Fraction) -> None:
        new_track = timeline_ops.split_at(project.tracks[track_index], position)
        super().__init__("Split", project, track_index, new_track)


class DeleteCommand(_TrackSwapCommand):
    """Remove an item, closing the gap (`ripple=True`) or leaving one behind.
    Deleting a gap itself always closes it -- see `timeline_ops.delete_item`."""

    def __init__(
        self, project: Project, track_index: int, item_index: int, *, ripple: bool
    ) -> None:
        track = project.tracks[track_index]
        new_track = timeline_ops.delete_item(track, item_index, ripple=ripple)
        if isinstance(track.items[item_index], Gap):
            text = "Delete empty space"
        else:
            text = "Delete and close gap" if ripple else "Delete"
        super().__init__(text, project, track_index, new_track)


class RemoveMediaCommand(QUndoCommand):
    """Remove every clip of one media file, on every track (leaving empty
    space) -- when that media is removed from the project's media bin."""

    def __init__(self, project: Project, media_id: str) -> None:
        super().__init__("Remove media")
        self._project = project
        self._old_tracks = list(project.tracks)
        self._new_tracks = [timeline_ops.remove_media(track, media_id) for track in project.tracks]

    def redo(self) -> None:
        self._project.tracks[:] = self._new_tracks

    def undo(self) -> None:
        self._project.tracks[:] = self._old_tracks


class RemoveGapsCommand(_TrackSwapCommand):
    """Close up every stretch of empty space in the track."""

    def __init__(self, project: Project, track_index: int) -> None:
        new_track = timeline_ops.remove_gaps(project.tracks[track_index])
        super().__init__("Delete all empty space", project, track_index, new_track)


class TrimCommand(_TrackSwapCommand):
    """Move a clip's start or end trim handle to `new_time` (source media time)."""

    def __init__(
        self,
        project: Project,
        track_index: int,
        item_index: int,
        edge: Literal["start", "end"],
        new_time: Fraction,
    ) -> None:
        new_track = timeline_ops.trim_item(project.tracks[track_index], item_index, edge, new_time)
        super().__init__("Trim", project, track_index, new_track)


class SetFitModeCommand(_TrackSwapCommand):
    """Change a clip's Fit Mode (black bars / blurred background / crop to fill)."""

    def __init__(
        self, project: Project, track_index: int, item_index: int, fit_mode: FitMode
    ) -> None:
        new_track = timeline_ops.set_fit_mode(project.tracks[track_index], item_index, fit_mode)
        super().__init__("Change fit mode", project, track_index, new_track)


class SetClipOptionsCommand(_TrackSwapCommand):
    """Change a clip's volume, mute, or fades (see `timeline_ops.set_clip_options`)."""

    def __init__(
        self,
        text: str,
        project: Project,
        track_index: int,
        item_index: int,
        *,
        volume: int | None = None,
        muted: bool | None = None,
        fade_in: bool | None = None,
        fade_out: bool | None = None,
    ) -> None:
        new_track = timeline_ops.set_clip_options(
            project.tracks[track_index],
            item_index,
            volume=volume,
            muted=muted,
            fade_in=fade_in,
            fade_out=fade_out,
        )
        super().__init__(text, project, track_index, new_track)


class InsertCommand(_TrackSwapCommand):
    """Place a new clip (or gap) onto the track, e.g. from the media bin."""

    def __init__(self, project: Project, track_index: int, index: int, item: TrackItem) -> None:
        new_track = timeline_ops.insert_item(project.tracks[track_index], index, item)
        super().__init__("Insert", project, track_index, new_track)


class MoveCommand(_TrackSwapCommand):
    """Reorder an item within the track (drag-to-reorder)."""

    def __init__(self, project: Project, track_index: int, from_index: int, to_index: int) -> None:
        new_track = timeline_ops.move_item(project.tracks[track_index], from_index, to_index)
        super().__init__("Move", project, track_index, new_track)
