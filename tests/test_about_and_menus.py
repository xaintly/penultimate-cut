from pathlib import Path

from penultimate_cut.ui import about_dialog
from penultimate_cut.ui.about_dialog import AboutDialog, app_version
from penultimate_cut.ui.main_window import MainWindow
from penultimate_cut.ui.timeline.widget import (
    EMPTY_TIMELINE_HINT,
    TRACK_HEIGHT,
    TRACK_TOP,
    TimelineWidget,
)


def test_version_comes_from_pyproject_when_running_from_source() -> None:
    text = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{app_version()}"' in text


def test_version_falls_back_to_package_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(about_dialog, "_PYPROJECT", tmp_path / "missing.toml")
    monkeypatch.setattr(about_dialog, "version", lambda _name: "9.9")
    assert app_version() == "9.9"


def test_about_dialog_names_license_source_and_version(qapp) -> None:
    dialog = AboutDialog()
    text = dialog.text.text()
    assert f"Penultimate Cut {app_version()}" in text
    assert "GNU General Public License" in text
    assert about_dialog.SOURCE_URL in text
    assert "FFmpeg" in text and "mpv" in text


def test_view_and_help_menus(qapp) -> None:
    window = MainWindow(vo="null")
    try:
        menus = {
            action.text(): action.menu()
            for action in window.menuBar().actions()
            if action.menu() is not None
        }
        assert list(menus) == ["&File", "&Edit", "&View", "&Play", "&Help"]
        view = {a.text(): a for a in menus["&View"].actions()}
        assert view["Zoom In"].shortcut().toString() == "+"
        assert view["Zoom Out"].shortcut().toString() == "-"

        baseline = window.timeline._canvas._view.pixels_per_second
        view["Zoom In"].trigger()
        assert window.timeline._canvas._view.pixels_per_second > baseline
        view["Zoom Out"].trigger()
        assert window.timeline._canvas._view.pixels_per_second == baseline

        assert [a.text() for a in menus["&Help"].actions()] == ["About Penultimate Cut"]
    finally:
        window.undo_stack.setClean()
        window.close()


def test_empty_timeline_shows_a_hint(qapp) -> None:
    widget = TimelineWidget()
    try:
        widget.resize(600, 150)
        widget.show()
        qapp.processEvents()
        image = widget._canvas.grab().toImage()
        # Light text pixels in the middle of the (otherwise near-black) track.
        middle = range(TRACK_TOP + 20, TRACK_TOP + TRACK_HEIGHT - 20)
        text_pixels = sum(
            1 for y in middle for x in range(150, 450) if image.pixelColor(x, y).red() > 100
        )
        assert text_pixels > 50
        assert EMPTY_TIMELINE_HINT.startswith("Drag videos here")
    finally:
        widget.shutdown()
