from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtGui import QColor

from penultimate_cut.model.project import Gap, Project, Title, Track, new_title_clip
from penultimate_cut.ui.titles import title_image, title_image_file, title_images


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENULTIMATE_CUT_CACHE_DIR", str(tmp_path / "cache"))


def test_title_is_drawn_on_its_background_in_its_text_color(qapp) -> None:
    title = Title(text="ABC", background="#012456", text_color="#ffee00")
    image = title_image(title, 640, 360)

    assert (image.width(), image.height()) == (640, 360)
    middle = QColor(image.pixel(10, 180))  # left edge, clear of the text
    assert abs(middle.blue() - 0x56) < 25 and middle.red() < 20
    colors = {image.pixel(x, y) for x in range(200, 440, 2) for y in range(150, 210, 2)}
    assert QColor("#ffee00").rgb() in colors  # the text, centered


def test_long_text_shrinks_to_fit(qapp) -> None:
    long = Title(text="A much longer title that has to wrap onto several lines to fit")
    image = title_image(long, 320, 180)
    margin = [image.pixel(x, y) for x in range(320) for y in (0, 1, 178, 179)]
    edge = [image.pixel(x, y) for x in (0, 1, 318, 319) for y in range(180)]
    assert QColor("#ffffff").rgb() not in margin + edge


def test_pictures_are_cached_on_disk_by_title_and_size(qapp, tmp_path: Path) -> None:
    title = Title(text="Cached")
    path = title_image_file(title, 320, 180)
    assert path.exists() and path.is_relative_to(tmp_path)
    stamp = path.stat().st_mtime_ns

    assert title_image_file(title, 320, 180) == path
    assert path.stat().st_mtime_ns == stamp  # not drawn again
    assert title_image_file(title, 180, 320) != path
    assert title_image_file(Title(text="Other"), 320, 180) != path


def test_title_images_covers_every_title_in_the_project(qapp) -> None:
    first, second = Title(text="One"), Title(text="Two")
    track = Track(
        kind="video",
        items=[new_title_clip(first), Gap(duration=Fraction(1)), new_title_clip(second)],
    )
    images = title_images(Project(frame_rate=Fraction(30), tracks=[track]), 320, 180)
    assert set(images) == {first, second}
    assert all(path.exists() for path in images.values())
