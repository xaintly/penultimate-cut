"""App-wide settings and default locations -- things that belong to the
user's machine, not to any one project (so never saved in the `.otio`).

Settings go through `QSettings` (on Windows: the registry, under
HKCU\\Software\\PenultimateCut). Uses `QSettings.defaultFormat()` so tests
can redirect everything to a throwaway INI file (see tests/conftest.py)
instead of touching the real registry.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

_ORGANIZATION = "PenultimateCut"
_APPLICATION = "Penultimate Cut"
_FOLDER_NAME = "PenultimateCut"

DEFAULT_PREVIEW_VOLUME = 100


def _settings() -> QSettings:
    return QSettings(
        QSettings.defaultFormat(), QSettings.Scope.UserScope, _ORGANIZATION, _APPLICATION
    )


def default_folder() -> Path:
    """Where projects and exported videos go unless the user picks somewhere
    else: `Videos\\PenultimateCut`. Created on first use. Asks Qt for the
    Videos folder rather than assuming `~/Videos`, since Windows lets users
    move it (e.g. to OneDrive or another drive). Falls back to the home
    folder if it can't be created."""
    override = os.environ.get("PENULTIMATE_CUT_DEFAULT_DIR")
    if override:
        folder = Path(override)
    else:
        videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
        folder = Path(videos or Path.home() / "Videos") / _FOLDER_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path.home()
    return folder


def unique_path(path: Path) -> Path:
    """`path`, or `name (2).ext`, `name (3).ext`, ... -- the first that
    doesn't exist yet, the same naming Windows Explorer uses for copies."""
    candidate = path
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def load_preview_volume() -> int:
    value = _settings().value("preview/volume", DEFAULT_PREVIEW_VOLUME)
    try:
        return max(0, min(100, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_VOLUME


def save_preview_volume(volume: int) -> None:
    _settings().setValue("preview/volume", volume)
