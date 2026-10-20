"""Per-media cache directory, keyed by path + size + mtime.

Keying on size and mtime (not just path) means replacing the file at a given
path — re-exporting over it, say — invalidates the cache automatically
instead of serving stale thumbnails or waveforms.
"""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path


def cache_dir_for(media_path: Path) -> Path:
    """Return the cache directory for `media_path`, creating it if needed."""
    resolved = media_path.resolve()
    stat = resolved.stat()
    key = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    cache_dir = _cache_root() / digest
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_root() -> Path:
    override = os.environ.get("PENULTIMATE_CUT_CACHE_DIR")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "PenultimateCut" / "cache" / "media"
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "penultimate_cut" / "media"
