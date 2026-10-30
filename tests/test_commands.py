from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtGui import QUndoStack

from penultimate_cut.commands.timeline_commands import (
    DeleteCommand,
    InsertCommand,
    MoveCommand,
    SetClipOptionsCommand,
    SetFitModeCommand,
    SetTitleCommand,
    SplitCommand,
    TrimCommand,
)
from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Title, Track, new_title_clip
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


def test_set_fit_mode_command_and_undo_restores() -> None:
    clip = make_clip(duration=Fraction(5))
    project = make_project(clip)
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(SetFitModeCommand(project, 0, 0, "blurred_background"))

    assert project.tracks[0].items[0].fit_mode == "blurred_background"

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


def test_set_clip_options_command_undoes() -> None:
    project = make_project(make_clip())
    original_track = project.tracks[0]
    stack = QUndoStack()

    stack.push(SetClipOptionsCommand("Mute", project, 0, 0, muted=True))

    assert project.tracks[0].items[0].muted is True
    assert stack.undoText() == "Mute"
    stack.undo()
    assert project.tracks[0] is original_track


def test_typing_into_a_title_is_one_undo_step() -> None:
    title_clip = new_title_clip()
    project = make_project(title_clip, make_clip())
    stack = QUndoStack()
    changes: list[int] = []
    stack.indexChanged.connect(changes.append)

    for text in ("H", "Hello", "Hello there"):
        stack.push(SetTitleCommand("Edit title", project, 0, 0, Title(text=text), typing=True))

    assert stack.count() == 1
    assert len(changes) == 3  # the preview hears about every pause
    assert project.tracks[0].items[0].media_ref.title == Title(text="Hello there")
    stack.undo()
    assert project.tracks[0].items[0].media_ref.title == Title()
    stack.redo()
    assert project.tracks[0].items[0].media_ref.title == Title(text="Hello there")


def test_title_color_changes_and_other_titles_are_separate_undo_steps() -> None:
    project = make_project(new_title_clip(), new_title_clip())
    stack = QUndoStack()
    stack.push(SetTitleCommand("Edit title", project, 0, 0, Title(text="A"), typing=True))
    red = Title(text="A", background="#ff0000")
    stack.push(SetTitleCommand("Change title background", project, 0, 0, red))
    stack.push(SetTitleCommand("Edit title", project, 0, 0, Title(text="B"), typing=True))
    stack.push(SetTitleCommand("Edit title", project, 0, 1, Title(text="C"), typing=True))

    assert stack.count() == 4
    assert stack.undoText() == "Edit title"
    stack.undo()
    stack.undo()
    assert project.tracks[0].items[0].media_ref.title == red
