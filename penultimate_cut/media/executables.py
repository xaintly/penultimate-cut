"""Locate bundled ffmpeg/ffprobe/libmpv, falling back to PATH on dev machines."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path


class ExecutableNotFoundError(RuntimeError):
    pass


def vendor_dir() -> Path:
    """Where vendored binaries (ffmpeg.exe, ffprobe.exe, libmpv-2.dll) live:
    next to the frozen .exe in a PyInstaller build (see
    packaging/penultimate_cut.spec), or the repo root's own vendor/ in dev
    (populated by scripts/fetch_vendor.ps1). `app.py` also reads this to
    put vendor/ on PATH before anything imports `mpv` -- python-mpv's own
    DLL search never looks here on its own."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "vendor"
    return Path(__file__).resolve().parents[2] / "vendor"


def locate(name: str) -> str:
    """`name` is the base executable name without extension, e.g. "ffmpeg" or "ffprobe"."""
    exe_name = f"{name}.exe" if platform.system() == "Windows" else name
    vendored = vendor_dir() / exe_name
    if vendored.exists():
        return str(vendored)
    found = shutil.which(name)
    if found:
        return found
    raise ExecutableNotFoundError(f"{name} not found (expected in vendor/ or on PATH)")
