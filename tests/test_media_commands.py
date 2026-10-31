from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtGui import QUndoStack

from penultimate_cut.commands.media_commands import (
    AddMediaCommand,
    MoveMediaCommand,
    RemoveMediaCommand,
)
from penultimate_cut.model.media_ops import (
    MediaOpError,
    add_media,
    move_media,
    remove_media,
    timeline_media,
)
from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Track, new_title_clip


def make_media(name: str) -> MediaRef:
    return MediaRef(
        path=Path(name),
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )


def names(media: list[MediaRef]) -> list[str]:
    return [media_ref.path.name for media_ref in media]


# --- media_ops -------------------------------------------------------------------


def test_add_media_appends_and_skips_what_is_already_there() -> None:
    a, b, c = make_media("a"), make_media("b"), make_media("c")
    media = [a]
    assert names(add_media(media, [b, a, c, b])) == ["a", "b", "c"]
    assert media == [a]  # not mutated


@pytest.mark.parametrize(
    ("from_index", "before_index", "expected"),
    [
        (0, 3, ["b", "c", "a"]),  # to the end
        (0, 2, ["b", "a", "c"]),
        (2, 0, ["c", "a", "b"]),  # to the start
        (1, 1, ["a", "b", "c"]),  # before itself: stays
        (1, 2, ["a", "b", "c"]),  # before the next one: stays
    ],
)
def test_move_media_puts_the_entry_before_another(
    from_index: int, before_index: int, expected: list[str]
) -> None:
    media = [make_media("a"), make_media("b"), make_media("c")]
    assert names(move_media(media, from_index, before_index)) == expected
    assert names(media) == ["a", "b", "c"]


def test_move_media_rejects_indexes_out_of_range() -> None:
    media = [make_media("a")]
    with pytest.raises(MediaOpError):
        move_media(media, 1, 0)
    with pytest.raises(MediaOpError):
        move_media(media, 0, 2)


def test_remove_media_by_id() -> None:
    a, b = make_media("a"), make_media("b")
    assert remove_media([a, b], a.id) == [b]


def test_timeline_media_is_in_order_of_first_use_without_titles() -> None:
    a, b = make_media("a"), make_media("b")
    items = [
        Clip(media_ref=b, source_in=Fraction(0), source_out=Fraction(1)),
        new_title_clip(),
        Gap(duration=Fraction(1)),
        Clip(media_ref=a, source_in=Fraction(0), source_out=Fraction(1)),
        Clip(media_ref=b, source_in=Fraction(1), source_out=Fraction(2)),
    ]
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])
    assert timeline_media(project) == [b, a]


# --- commands ----------------------------------------------------------------------


def test_add_media_command_adds_and_undoes() -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video")])
    a = make_media("a")
    stack = QUndoStack()
    stack.push(AddMediaCommand(project, [a]))
    assert project.media == [a]
    stack.undo()
    assert project.media == []


def test_imports_from_one_batch_merge_into_one_undo_step() -> None:
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video")])
    a, b, c = make_media("a"), make_media("b"), make_media("c")
    stack = QUndoStack()
    stack.push(AddMediaCommand(project, [a], batch=1))
    stack.push(AddMediaCommand(project, [b], batch=1))
    stack.push(AddMediaCommand(project, [c], batch=2))
    assert names(project.media) == ["a", "b", "c"]
    assert stack.count() == 2

    stack.undo()
    assert names(project.media) == ["a", "b"]
    stack.undo()
    assert project.media == []


def test_move_media_command_moves_and_undoes() -> None:
    a, b, c = make_media("a"), make_media("b"), make_media("c")
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video")], media=[a, b, c])
    stack = QUndoStack()
    stack.push(MoveMediaCommand(project, 0, 3))
    assert names(project.media) == ["b", "c", "a"]
    stack.undo()
    assert names(project.media) == ["a", "b", "c"]


def test_remove_media_command_removes_it_from_the_bin_and_the_timeline() -> None:
    a, b = make_media("a"), make_media("b")
    clip_a = Clip(media_ref=a, source_in=Fraction(0), source_out=Fraction(2))
    clip_b = Clip(media_ref=b, source_in=Fraction(0), source_out=Fraction(2))
    project = Project(
        frame_rate=Fraction(30),
        tracks=[Track(kind="video", items=[clip_a, clip_b])],
        media=[a, b],
    )
    stack = QUndoStack()

    stack.push(RemoveMediaCommand(project, a.id))
    assert project.media == [b]
    assert isinstance(project.tracks[0].items[0], Gap)
    assert project.tracks[0].items[1] is clip_b

    stack.undo()
    assert project.media == [a, b]
    assert project.tracks[0].items == [clip_a, clip_b]
