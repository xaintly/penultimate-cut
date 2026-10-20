"""Synthetic test media, generated with ffmpeg lavfi sources.

Never commit real media to this repo — fixtures here generate short clips
on the fly (and cache them for the test session) so tests don't depend on
copyrighted sample files.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

# Module level, not inside a fixture, and unconditional (no "if a test needs
# it" gating): some test modules import penultimate_cut.ui.preview_widget (and
# so penultimate_cut.preview.player, and so `mpv`) at their own module level,
# which pytest resolves during *collection* -- before any fixture, including
# an autouse session one, ever runs. Both of the following have to be in
# place *before* that first `import mpv`, confirmed empirically (not just by
# reasoning about it) by reproducing a crash/hang otherwise:
#
# - A DISPLAY (with a GPU or `LIBGL_ALWAYS_SOFTWARE=1`): libmpv/Mesa do a
#   one-time native init keyed off whatever's in the environment right then;
#   setting DISPLAY later in a fixture doesn't undo whatever got cached.
# - A QApplication already existing in the process: importing `mpv` before
#   one exists segfaults inside libmpv's own init, reliably, even though the
#   crash looks unrelated to Qt at all.
_xvfb_process: subprocess.Popen | None = None

if not os.environ.get("DISPLAY"):
    _xvfb = shutil.which("Xvfb")
    _display_num = next((n for n in range(90, 200) if not Path(f"/tmp/.X{n}-lock").exists()), None)
    if _xvfb is not None and _display_num is not None:
        _xvfb_process = subprocess.Popen(
            [_xvfb, f":{_display_num}", "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = f":{_display_num}"
        # Xvfb has no real (or virtual) GPU, and mpv's default vo crashes trying
        # to create a hardware GL context against it -- force Mesa's software
        # rasterizer. Only reached when we're the ones starting Xvfb (no
        # DISPLAY was set at all), so this never overrides a real desktop
        # session's own GL setup.
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        time.sleep(0.5)
        atexit.register(_xvfb_process.terminate)

_qapp_instance = QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """The one QApplication instance Qt allows per process, shared by all tests."""
    return _qapp_instance


@pytest.fixture(scope="session")
def make_video_clip(media_dir: Path):
    """Factory fixture: `make_video_clip(name, duration=2, fps=30, size="320x240")` -> mp4 path.

    `fps` can be a plain number or a rational string like `"30000/1001"` for NTSC rates.
    """

    def _make(name: str, duration: float = 2.0, fps: int | str = 30, size: str = "320x240") -> Path:
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


@pytest.fixture(scope="session")
def make_silent_video_clip(media_dir: Path):
    """Factory fixture: `make_silent_video_clip(name, duration=2, size="320x240")` -> mp4 path
    with a video stream but no audio stream at all."""

    def _make(name: str, duration: float = 2.0, size: str = "320x240") -> Path:
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
                f"testsrc2=duration={duration}:size={size}:rate=30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(out_path),
            ],
            check=True,
        )
        return out_path

    return _make


@pytest.fixture(scope="session")
def make_audio_clip(media_dir: Path):
    """Factory fixture: `make_audio_clip(name, duration=2)` -> path to an audio-only m4a."""

    def _make(name: str, duration: float = 2.0) -> Path:
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
                f"sine=duration={duration}",
                "-c:a",
                "aac",
                str(out_path),
            ],
            check=True,
        )
        return out_path

    return _make


@pytest.fixture(scope="session")
def make_still_image(media_dir: Path):
    """Factory fixture: `make_still_image(name, size="320x240")` -> path to a single-frame image."""

    def _make(name: str, size: str = "320x240") -> Path:
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
                f"color=c=blue:s={size}",
                "-frames:v",
                "1",
                str(out_path),
            ],
            check=True,
        )
        return out_path

    return _make
