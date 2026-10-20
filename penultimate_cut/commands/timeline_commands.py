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
from penultimate_cut.model.project import Project, Track, TrackItem


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
    """Remove an item, closing the gap (`ripple=True`) or leaving one behind."""

    def __init__(
        self, project: Project, track_index: int, item_index: int, *, ripple: bool
    ) -> None:
        new_track = timeline_ops.delete_item(project.tracks[track_index], item_index, ripple=ripple)
        text = "Delete" if ripple else "Delete (leave gap)"
        super().__init__(text, project, track_index, new_track)


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
