from fractions import Fraction
from pathlib import Path

import pytest

from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Title, Track, new_title_clip
from penultimate_cut.preview.edl import PICTURE_FPS
from penultimate_cut.preview.pictures import image_list, picture_lists


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache"))


def picture(path: Path) -> MediaRef:
    return MediaRef(
        path=path,
        relative_path=None,
        duration=Fraction(24 * 60 * 60),
        frame_rate=None,
        has_video=True,
        has_audio=False,
    )


def video(path: Path) -> MediaRef:
    return MediaRef(
        path=path,
        relative_path=None,
        duration=Fraction(10),
        frame_rate=Fraction(30),
        has_video=True,
        has_audio=True,
    )


def clip(media: MediaRef, seconds: int) -> Clip:
    return Clip(media_ref=media, source_in=Fraction(0), source_out=Fraction(seconds))


def test_lists_cover_each_pictures_longest_clip(tmp_path: Path) -> None:
    photo = picture(tmp_path / "Beach.JPG")
    odd = picture(tmp_path / "scan.jfif")  # mpv can't tell its format
    title_clip = new_title_clip(Title(text="Hi"))
    pending_title = new_title_clip(Title(text="Not drawn"))
    items = [
        clip(photo, 4),
        Gap(duration=Fraction(1)),
        clip(photo, 12),
        clip(video(tmp_path / "a.mp4"), 3),
        clip(odd, 2),
        title_clip,
        pending_title,
    ]
    project = Project(frame_rate=Fraction(30), tracks=[Track(kind="video", items=items)])
    drawn = tmp_path / "title.png"

    lists = picture_lists(project, {title_clip.media_ref.title: drawn})

    assert set(lists) == {photo.id, title_clip.media_ref.id}
    photo_lines = lists[photo.id].read_text(encoding="utf-8").splitlines()
    assert photo_lines == [str(photo.path)] * (20 * PICTURE_FPS + 1)  # 12 s, in 10 s steps
    title_lines = lists[title_clip.media_ref.id].read_text(encoding="utf-8").splitlines()
    assert set(title_lines) == {str(drawn)} and len(title_lines) == 10 * PICTURE_FPS + 1


def test_lists_are_written_once_and_shared_by_lengths_in_the_same_step(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    first = image_list(photo, Fraction(3))
    stamp = first.stat().st_mtime_ns

    assert image_list(photo, Fraction(9)) == first
    assert first.stat().st_mtime_ns == stamp
    assert image_list(photo, Fraction(11)) != first
    assert image_list(tmp_path / "other.png", Fraction(3)) != first
    assert not list(first.parent.glob("*.partial"))
