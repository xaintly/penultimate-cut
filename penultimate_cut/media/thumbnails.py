"""Filmstrip thumbnails: a handful of JPEG frames per clip, cached on disk.

Still images are a special case: every point in their "clip" looks the same,
and ffmpeg can't seek (`-ss`) into a single-frame source at all — so one
frame is extracted at a fixed position and reused for every requested slot,
instead of seeking per slot like a real video.
"""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.cache import cache_dir_for
from penultimate_cut.media.executables import ExecutableNotFoundError, locate

DEFAULT_WIDTH = 160


class ThumbnailError(RuntimeError):
    """ffmpeg couldn't extract a frame."""


def filmstrip(
    media_path: Path,
    duration: Fraction,
    count: int,
    *,
    start: Fraction = Fraction(0),
    is_still_image: bool = False,
    width: int = DEFAULT_WIDTH,
) -> list[Path]:
    """Return `count` cached frame thumbnails spanning `[start, start+duration)`.

    `start` defaults to 0 (the media bin's use: sample across the whole
    file), but a trimmed timeline clip passes its own `source_in` here so
    the filmstrip reflects what the clip actually shows, not the start of
    the source file.

    Frames land at the midpoint of each of `count` equal segments, so a
    single thumbnail (`count=1`) comes from the middle of the clip rather
    than its possibly-black first frame. Any frame not already cached is
    extracted now.
    """
    if count < 1:
        raise ValueError("count must be at least 1")

    cache_dir = cache_dir_for(media_path)

    if is_still_image:
        out_path = cache_dir / f"still_{width}w.jpg"
        if not out_path.exists():
            _extract_frame(media_path, None, width, out_path)
        return [out_path] * count

    segment = duration / count
    start_us = int(start * 1_000_000)
    duration_us = int(duration * 1_000_000)
    paths = []
    for index in range(count):
        timestamp = start + segment * index + segment / 2
        out_path = cache_dir / f"frame_{index}_of_{count}_{width}w_s{start_us}_d{duration_us}.jpg"
        if not out_path.exists():
            _extract_frame(media_path, timestamp, width, out_path)
        paths.append(out_path)
    return paths


def thumbnail(
    media_path: Path,
    duration: Fraction,
    *,
    is_still_image: bool = False,
    width: int = DEFAULT_WIDTH,
) -> Path:
    """A single representative thumbnail, from the middle of the clip."""
    return filmstrip(media_path, duration, count=1, is_still_image=is_still_image, width=width)[0]


def _extract_frame(
    media_path: Path, timestamp: Fraction | None, width: int, out_path: Path
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg = locate("ffmpeg")
    except ExecutableNotFoundError as exc:
        raise ThumbnailError(str(exc)) from exc

    args = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if timestamp is not None:
        args += ["-ss", str(float(timestamp))]
    args += [
        "-i",
        str(media_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-vf",
        f"scale={width}:-2,format=yuvj420p",
        "-y",
        str(out_path),
    ]
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise ThumbnailError(f"{media_path}: ffmpeg failed: {exc.stderr.strip()}") from exc
