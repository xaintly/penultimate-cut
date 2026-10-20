"""Locate bundled ffmpeg/ffprobe executables, falling back to PATH on dev machines."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path


class ExecutableNotFoundError(RuntimeError):
    pass


def locate(name: str) -> str:
    """`name` is the base executable name without extension, e.g. "ffmpeg" or "ffprobe"."""
    exe_name = f"{name}.exe" if platform.system() == "Windows" else name
    vendored = Path(__file__).resolve().parents[2] / "vendor" / exe_name
    if vendored.exists():
        return str(vendored)
    found = shutil.which(name)
    if found:
        return found
    raise ExecutableNotFoundError(f"{name} not found (expected in vendor/ or on PATH)")
