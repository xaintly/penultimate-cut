"""ffprobe wrapper: inspect a media file on disk and report what it contains.

NOTE: phone-recorded rotation metadata (the mp4 "display matrix") isn't read
here. Handling it belongs with Phase 2's Rotate feature, which needs the same
information to drive the Fit Mode default — flagging here rather than adding
a half-built heuristic now.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.executables import ExecutableNotFoundError, locate

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
    width = video_stream.get("width") if video_stream else None
    height = video_stream.get("height") if video_stream else None

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
        )
    except subprocess.CalledProcessError as exc:
        raise ProbeError(f"{path}: ffprobe failed: {exc.stderr.strip()}") from exc
    try:
        parsed: dict = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{path}: could not parse ffprobe output") from exc
    return parsed
