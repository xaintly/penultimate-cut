"""Autosave: a periodic snapshot of the current project, written beside the
project file (as `<project>.otio.autosave`, per CLAUDE.md), or to a single
fixed per-install slot for a project that's never been saved -- the app is
single-document, so there's only ever one "current, unsaved" project to
protect.

Deliberately separate from the project file itself: Save/Save As never read
or write an autosave path, and recovering one just loads its content into
memory under the *original* project path (or none) -- nothing is written to
disk until the user explicitly saves. Policy (when to write, when to offer
recovery, when to discard) lives in the UI layer; this module is just the
file-level mechanics, so it can be tested without Qt.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from penultimate_cut.io.otio_io import LoadResult, load_project, save_project
from penultimate_cut.model.project import Project


def autosave_path_for(project_path: Path | None) -> Path:
    """Where the autosave for `project_path` lives."""
    if project_path is not None:
        return project_path.with_name(project_path.name + ".autosave")
    return _autosave_root() / "Untitled.otio.autosave"


def write_autosave(project: Project, project_path: Path | None) -> None:
    autosave_path = autosave_path_for(project_path)
    autosave_path.parent.mkdir(parents=True, exist_ok=True)
    save_project(project, autosave_path)


def find_recoverable_autosave(project_path: Path | None) -> Path | None:
    """The autosave's path if one exists and is worth offering to recover:
    newer than `project_path`'s own last-saved time, or there being no
    project file at all yet (`project_path` is None, or doesn't exist) --
    in that case the autosave is the *only* copy of that work, so any
    autosave at all is recoverable."""
    autosave_path = autosave_path_for(project_path)
    if not autosave_path.exists():
        return None
    if project_path is not None and project_path.exists():
        if autosave_path.stat().st_mtime <= project_path.stat().st_mtime:
            return None
    return autosave_path


def load_autosave(autosave_path: Path) -> LoadResult:
    return load_project(autosave_path)


def discard_autosave(project_path: Path | None) -> None:
    """Remove the autosave for `project_path`, if any -- e.g. once it's been
    superseded by an explicit save, or the user declined to recover it."""
    autosave_path_for(project_path).unlink(missing_ok=True)


def _autosave_root() -> Path:
    override = os.environ.get("PENULTIMATE_CUT_AUTOSAVE_DIR")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "PenultimateCut" / "autosave"
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "penultimate_cut" / "autosave"
