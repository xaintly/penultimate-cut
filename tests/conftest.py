"""Synthetic test media, generated with ffmpeg lavfi sources.

Never commit real media to this repo — fixtures here generate short clips
on the fly (and cache them for the test session) so tests don't depend on
copyrighted sample files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def make_video_clip(media_dir: Path):
    """Factory fixture: `make_video_clip(name, duration=2, fps=30, size="320x240")` -> mp4 path."""

    def _make(name: str, duration: float = 2.0, fps: int = 30, size: str = "320x240") -> Path:
        out_path = media_dir / name
        if out_path.exists():
            return out_path
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=duration={duration}:size={size}:rate={fps}",
                "-f",
                "lavfi",
                "-i",
                f"sine=duration={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(out_path),
            ],
            check=True,
        )
        return out_path

    return _make
