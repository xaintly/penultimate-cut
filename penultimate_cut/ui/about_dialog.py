"""Help > About: version, license, source code, and what's bundled.

The GPL asks that people receiving the app can find its license and source,
and the bundled ffmpeg and libmpv licenses ask the same; this is where.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from penultimate_cut.media.executables import licenses_dir

SOURCE_URL = "https://github.com/xaintly/penultimate-cut"
_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def app_version() -> str:
    """The version in pyproject.toml: read directly when running from source
    (an old editable install's metadata can be stale), otherwise from the
    package's metadata, which the PyInstaller spec bundles."""
    if _PYPROJECT.is_file():
        with _PYPROJECT.open("rb") as file:
            return str(tomllib.load(file)["project"]["version"])
    try:
        return version("penultimate-cut")
    except PackageNotFoundError:
        return "(development)"


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Penultimate Cut")

        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(64, 64))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)

        folder = licenses_dir()
        if folder is not None and folder.is_dir():
            licenses = (
                f'Their licenses are in the <a href="{QUrl.fromLocalFile(str(folder)).toString()}">'
                "licenses folder</a>."
            )
        else:
            licenses = "Their licenses come with the installed app."
        self.text = QLabel(
            f"<h3>Penultimate Cut {app_version()}</h3>"
            "<p>A simple video editor.<br><i>Not the final cut. Just the one you turn in.</i></p>"
            "<p>Free software under the GNU General Public License, version 3 or later: "
            "you may share and change it under its terms.<br>"
            f'Source code: <a href="{SOURCE_URL}">{SOURCE_URL.removeprefix("https://")}</a></p>'
            "<p>Built with FFmpeg, mpv, Qt for Python, OpenTimelineIO and Python. "
            f"{licenses}</p>"
        )
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setOpenExternalLinks(True)
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(380)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(icon)
        row.addWidget(self.text, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(buttons)
