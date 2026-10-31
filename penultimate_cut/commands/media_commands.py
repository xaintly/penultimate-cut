"""QUndoCommand wrappers around model/media_ops.py: the media bin's edits.

The bin is part of the project (`Project.media`), so importing, reordering,
and removing media are edits like any other: undoable, marking the project
unsaved, and autosaved. Same before/after snapshot approach as
commands/timeline_commands.py.
"""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from penultimate_cut.model import media_ops, timeline_ops
from penultimate_cut.model.project import MediaRef, Project


class _MediaSwapCommand(QUndoCommand):
    """Base for commands that replace `project.media` wholesale."""

    def __init__(self, text: str, project: Project, new_media: list[MediaRef]) -> None:
        super().__init__(text)
        self._project = project
        self._old_media = list(project.media)
        self._new_media = new_media

    def redo(self) -> None:
        self._project.media = list(self._new_media)

    def undo(self) -> None:
        self._project.media = list(self._old_media)


class AddMediaCommand(_MediaSwapCommand):
    """Add imported media to the end of the bin.

    Files picked together finish importing one at a time (each is probed on
    a worker thread); giving them the same `batch` merges them into one
    "Import" step, as long as nothing else was done in between."""

    _MERGE_ID = 2

    def __init__(self, project: Project, media: list[MediaRef], batch: int | None = None) -> None:
        super().__init__("Import", project, media_ops.add_media(project.media, media))
        self._batch = batch

    def id(self) -> int:
        return self._MERGE_ID if self._batch is not None else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not (isinstance(other, AddMediaCommand) and other._batch == self._batch):
            return False
        self._new_media = other._new_media
        return True


class MoveMediaCommand(_MediaSwapCommand):
    """Move a bin entry to just before another (see `media_ops.move_media`)."""

    def __init__(self, project: Project, from_index: int, before_index: int) -> None:
        new_media = media_ops.move_media(project.media, from_index, before_index)
        super().__init__("Move media", project, new_media)


class RemoveMediaCommand(QUndoCommand):
    """Remove media from the bin, and every clip of it on every track
    (leaving empty space)."""

    def __init__(self, project: Project, media_id: str) -> None:
        super().__init__("Remove media")
        self._project = project
        self._old_tracks = list(project.tracks)
        self._new_tracks = [timeline_ops.remove_media(track, media_id) for track in project.tracks]
        self._old_media = list(project.media)
        self._new_media = media_ops.remove_media(project.media, media_id)

    def redo(self) -> None:
        self._project.tracks[:] = self._new_tracks
        self._project.media = list(self._new_media)

    def undo(self) -> None:
        self._project.tracks[:] = self._old_tracks
        self._project.media = list(self._old_media)
