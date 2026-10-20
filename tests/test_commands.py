from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtGui import QUndoStack

from penultimate_cut.commands.timeline_commands import (
    DeleteCommand,
    InsertCommand,
    MoveCommand,
    SplitCommand,
    TrimCommand,
)
from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Track
from penultimate_cut.model.timeline_ops import TimelineOpError


def make_clip(path: str = "a.mp4", duration: Fraction = Fraction(10)) -> Clip:
    media = MediaRef(
        path=Path(path),
        relative_path=None,
        duration=duration,
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )
    return Clip(media_ref=media, source_in=Fraction(0), source_out=duration)


def make_project(*clips: Clip | Gap) -> Project:
    return Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=list(clips))])


def test_split_command_splits_on_push_and_restores_on_undo() -> None:
    clip = make_clip(duration=Fraction(10))
    project = make_project(clip)
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(SplitCommand(project, 0, Fraction(4)))

    assert len(project.tracks[0].items) == 2
    assert project.tracks[0].items[0].duration == Fraction(4)
    assert project.tracks[0].items[1].duration == Fraction(6)

    stack.undo()
    assert project.tracks[0] is original_track


def test_delete_command_ripple_closes_gap_and_undo_restores() -> None:
    clips = [make_clip(duration=Fraction(5)), make_clip(duration=Fraction(3))]
    project = make_project(*clips)
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(DeleteCommand(project, 0, 0, ripple=True))

    assert len(project.tracks[0].items) == 1
    assert project.tracks[0].duration == Fraction(3)

    stack.undo()
    assert project.tracks[0] is original_track


def test_delete_command_leave_gap_and_undo_restores() -> None:
    clips = [make_clip(duration=Fraction(5)), make_clip(duration=Fraction(3))]
    project = make_project(*clips)
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(DeleteCommand(project, 0, 0, ripple=False))

    assert isinstance(project.tracks[0].items[0], Gap)
    assert project.tracks[0].duration == Fraction(8)

    stack.undo()
    assert project.tracks[0] is original_track


def test_trim_command_and_undo_restores() -> None:
    clips = [make_clip(duration=Fraction(10)), make_clip(duration=Fraction(5))]
    project = make_project(*clips)
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(TrimCommand(project, 0, 0, "start", Fraction(2)))

    assert project.tracks[0].items[0].duration == Fraction(8)
    assert project.tracks[0].duration == Fraction(13)

    stack.undo()
    assert project.tracks[0] is original_track


def test_move_command_and_undo_restores() -> None:
    clip_a, clip_b = make_clip("a.mp4"), make_clip("b.mp4")
    project = make_project(clip_a, clip_b)
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(MoveCommand(project, 0, 0, 1))

    assert project.tracks[0].items == [clip_b, clip_a]

    stack.undo()
    assert project.tracks[0] is original_track


def test_insert_command_appends_and_undo_restores() -> None:
    clip = make_clip(duration=Fraction(5))
    project = make_project(clip)
    original_track = project.tracks[0]
    new_clip = make_clip("b.mp4", duration=Fraction(3))
    stack = QUndoStack()

    stack.push(InsertCommand(project, 0, 1, new_clip))

    assert project.tracks[0].items == [clip, new_clip]

    stack.undo()
    assert project.tracks[0] is original_track


def test_invalid_command_raises_before_reaching_the_stack() -> None:
    clip = make_clip(duration=Fraction(10))
    project = make_project(clip)
    stack = QUndoStack()

    with pytest.raises(TimelineOpError):
        stack.push(TrimCommand(project, 0, 0, "end", Fraction(11)))

    assert stack.count() == 0
    assert project.tracks[0].items[0].duration == Fraction(10)


def test_command_text_is_set_for_undo_menu() -> None:
    clip = make_clip(duration=Fraction(10))
    project = make_project(clip)

    command = SplitCommand(project, 0, Fraction(4))

    assert command.text() == "Split"


def test_multiple_commands_undo_and_redo_in_order() -> None:
    clip = make_clip(duration=Fraction(10))
    project = make_project(clip)
    stack = QUndoStack()

    stack.push(SplitCommand(project, 0, Fraction(4)))
    stack.push(DeleteCommand(project, 0, 1, ripple=True))

    assert len(project.tracks[0].items) == 1
    assert project.tracks[0].items[0].duration == Fraction(4)

    stack.undo()
    assert len(project.tracks[0].items) == 2

    stack.undo()
    assert len(project.tracks[0].items) == 1
    assert project.tracks[0].items[0].duration == Fraction(10)

    stack.redo()
    stack.redo()
    assert len(project.tracks[0].items) == 1
    assert project.tracks[0].items[0].duration == Fraction(4)
