"""ffprobe wrapper: inspect a media file on disk and report what it contains.

Width/height are the **display** size -- what a viewer actually sees -- not
the stored frame size: phone video is often stored sideways with a rotation
tag (the mp4 "display matrix"), and some footage (DV, old camcorders) has
non-square pixels. ffmpeg and mpv both apply rotation automatically when
decoding, and export/filtergraph.py squares the pixels, so the display size
is the size every other part of the app actually works with.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.executables import NO_WINDOW, ExecutableNotFoundError, locate

STILL_IMAGE_DURATION = Fraction(24 * 60 * 60)
"""Still images have no intrinsic duration. This stands in for "as long as you
want" so `MediaRef.duration` (which bounds trimming) never needs to be Optional."""


class ProbeError(RuntimeError):
    """ffprobe couldn't run, or its output doesn't describe usable media."""


@dataclass
class MediaInfo:
    path: Path
    duration: Fraction
    has_video: bool
    has_audio: bool
    frame_rate: Fraction | None  # None for audio-only or still-image media
    width: int | None
    height: int | None

    @property
    def is_still_image(self) -> bool:
        return self.has_video and self.frame_rate is None


def probe(path: Path) -> MediaInfo:
    """Inspect `path` with ffprobe and return what it found.

    Raises `ProbeError` if ffprobe can't run, the file has neither a video
    nor an audio stream, or a duration can't be determined.
    """
    data = _ffprobe_json(path)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    has_video = video_stream is not None
    has_audio = audio_stream is not None
    if not has_video and not has_audio:
        raise ProbeError(f"{path}: no video or audio stream found")

    is_still_image = video_stream is not None and _is_still_image(data, video_stream)

    frame_rate = None if is_still_image or video_stream is None else _frame_rate(video_stream)
    width, height = _display_size(video_stream) if video_stream else (None, None)

    duration: Fraction
    if is_still_image:
        duration = STILL_IMAGE_DURATION
    else:
        found_duration = _duration(data, video_stream, audio_stream)
        if found_duration is None:
            raise ProbeError(f"{path}: could not determine duration")
        duration = found_duration

    return MediaInfo(
        path=path,
        duration=duration,
        has_video=has_video,
        has_audio=has_audio,
        frame_rate=frame_rate,
        width=width,
        height=height,
    )


def _is_still_image(data: dict, video_stream: dict) -> bool:
    """A still image's video stream has no frame count or duration of its own
    (unlike an animated gif, which does) and the container reports no overall
    duration either."""
    if "duration" in video_stream or "nb_frames" in video_stream:
        return False
    return "duration" not in data.get("format", {})


def _display_size(video_stream: dict) -> tuple[int | None, int | None]:
    width, height = video_stream.get("width"), video_stream.get("height")
    if not width or not height:
        return width, height
    sar = _ratio(video_stream.get("sample_aspect_ratio"))
    if sar is not None and sar != 1:
        # Same rounding as export/filtergraph.py's square-pixel scale (trunc to even).
        width = max(2, int(width * sar / 2) * 2)
    if _rotation(video_stream) % 180 == 90:
        width, height = height, width
    return width, height


def _rotation(video_stream: dict) -> int:
    """Degrees, from the display matrix (current ffprobe) or the older
    `rotate` tag; 0 if neither is there."""
    for side_data in video_stream.get("side_data_list", []):
        if "rotation" in side_data:
            try:
                return round(float(side_data["rotation"]))
            except (TypeError, ValueError):
                return 0
    try:
        return int(video_stream.get("tags", {}).get("rotate", 0))
    except (TypeError, ValueError):
        return 0


def _ratio(raw: str | None) -> Fraction | None:
    """ "4:3" -> Fraction(4, 3); None for missing or "0:1" (unknown)."""
    if not raw or ":" not in raw:
        return None
    num_str, _, den_str = raw.partition(":")
    try:
        num, den = int(num_str), int(den_str)
    except ValueError:
        return None
    if num <= 0 or den <= 0:
        return None
    return Fraction(num, den)


def _frame_rate(video_stream: dict) -> Fraction | None:
    raw = video_stream.get("r_frame_rate")  # e.g. "30000/1001", "25/1", "0/0"
    if not raw or "/" not in raw:
        return None
    num_str, _, den_str = raw.partition("/")
    try:
        num, den = int(num_str), int(den_str)
    except ValueError:
        return None
    if num == 0 or den == 0:
        return None
    return Fraction(num, den)


def _duration(data: dict, video_stream: dict | None, audio_stream: dict | None) -> Fraction | None:
    format_duration = data.get("format", {}).get("duration")
    if format_duration:
        return Fraction(format_duration)
    for stream in (video_stream, audio_stream):
        if stream and stream.get("duration"):
            return Fraction(stream["duration"])
    return None


def _ffprobe_json(path: Path) -> dict:
    if not path.exists():
        raise ProbeError(f"{path}: file does not exist")
    try:
        ffprobe = locate("ffprobe")
    except ExecutableNotFoundError as exc:
        raise ProbeError(str(exc)) from exc
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            creationflags=NO_WINDOW,
        )
    except subprocess.CalledProcessError as exc:
        raise ProbeError(f"{path}: ffprobe failed: {exc.stderr.strip()}") from exc
    try:
        parsed: dict = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{path}: could not parse ffprobe output") from exc
    return parsed
