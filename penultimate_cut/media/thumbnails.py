"""Thumbnails: single JPEG frames, cached on disk.

`thumbnail` is the media bin's one representative frame per file;
`frame_at` is one timeline filmstrip tile. The timeline asks only for the
tiles on screen, at times on a fixed grid in the source file (see
ui/timeline/widget.py), so the same cached frame serves every clip cut from
that file and every zoom level that lands on it.

Both are approximations, so they're made the quick way: the first keyframe
from the requested time on, decoded alone, instead of decoding every frame
from the keyframe before it up to the exact time. That's most of the cost
for phone video (4K, HEVC, keyframes a second or two apart). Past the last
keyframe there's no such frame, and the exact way is used instead.

Still images are a special case: every point in their "clip" looks the same,
and ffmpeg can't seek (`-ss`) into a single-frame source at all — so one
frame is extracted and reused for every requested time.
"""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.cache import cache_dir_for
from penultimate_cut.media.executables import (
    NO_WINDOW,
    ExecutableNotFoundError,
    local_files_only,
    locate,
)

DEFAULT_WIDTH = 160


class ThumbnailError(RuntimeError):
    """ffmpeg couldn't extract a frame."""


def thumbnail(
    media_path: Path,
    duration: Fraction,
    *,
    is_still_image: bool = False,
    width: int = DEFAULT_WIDTH,
) -> Path:
    """A single representative thumbnail, `width` px wide, from the middle
    of the clip rather than its possibly-black first frame."""
    cache_dir = cache_dir_for(media_path)
    if is_still_image:
        out_path = cache_dir / f"still_{width}w.jpg"
        timestamp = None
    else:
        timestamp = duration / 2
        out_path = cache_dir / f"thumb_{width}w_t{int(timestamp * 1_000_000)}.jpg"
    if not out_path.exists():
        _extract_frame(media_path, timestamp, f"scale={width}:-2", out_path)
    return out_path


def frame_at(
    media_path: Path,
    timestamp: Fraction,
    *,
    height: int,
    is_still_image: bool = False,
) -> Path:
    """The frame at `timestamp` seconds into `media_path`, `height` px tall
    (width follows the video's shape)."""
    cache_dir = cache_dir_for(media_path)
    if is_still_image:
        out_path = cache_dir / f"still_{height}h.jpg"
        seek: Fraction | None = None
    else:
        out_path = cache_dir / f"frame_{height}h_t{int(timestamp * 1_000_000)}.jpg"
        seek = timestamp
    if not out_path.exists():
        _extract_frame(media_path, seek, f"scale=-2:{height}", out_path)
    return out_path


def _extract_frame(
    media_path: Path, timestamp: Fraction | None, scale: str, out_path: Path
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg = locate("ffmpeg")
    except ExecutableNotFoundError as exc:
        raise ThumbnailError(str(exc)) from exc

    if timestamp is not None:
        try:
            _run_ffmpeg(ffmpeg, media_path, timestamp, scale, out_path, keyframe=True)
        except ThumbnailError:
            pass
        if out_path.exists():
            return
    _run_ffmpeg(ffmpeg, media_path, timestamp, scale, out_path, keyframe=False)
    if not out_path.exists():
        # e.g. a time past the last frame: ffmpeg exits cleanly with no output.
        raise ThumbnailError(f"{media_path}: no frame at {timestamp}")


def _run_ffmpeg(
    ffmpeg: str,
    media_path: Path,
    timestamp: Fraction | None,
    scale: str,
    out_path: Path,
    *,
    keyframe: bool,
) -> None:
    args = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if keyframe:
        # Decode keyframes only, on one thread: for a single frame, more threads
        # only cost time to start, and several of these run at once anyway.
        args += ["-skip_frame", "nokey", "-threads", "1"]
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
        f"{scale},format=yuvj420p",
        "-y",
        str(out_path),
    ]
    try:
        subprocess.run(
            local_files_only(args),
            check=True,
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
        )
    except subprocess.CalledProcessError as exc:
        raise ThumbnailError(f"{media_path}: ffmpeg failed: {exc.stderr.strip()}") from exc
