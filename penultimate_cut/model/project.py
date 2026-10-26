"""Pure data model for a project: media references, clips, tracks.

A `Track` holds a sequential list of `Clip` and `Gap` items — the same shape
OpenTimelineIO uses — so there is no separate "position on timeline" field to
keep in sync; an item's start is just the sum of the durations before it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Literal
from uuid import uuid4

from penultimate_cut.model.time import Rate

TrackKind = Literal["video", "audio"]
FitMode = Literal["black_bars", "blurred_background", "crop_to_fill"]

STILL_IMAGE_DEFAULT_DURATION = Fraction(5)
"""A still image's default clip length when first added to a track, per
CLAUDE.md's Phase 1 spec ("default duration (5s), adjustable by trimming")."""


@dataclass
class MediaRef:
    """A reference to a source file on disk. Never written to."""

    path: Path
    relative_path: str | None
    duration: Fraction
    frame_rate: Rate | None  # None for audio-only or still-image media
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def is_still_image(self) -> bool:
        return self.has_video and self.frame_rate is None


@dataclass
class Clip:
    """A trimmed reference to a slice of a `MediaRef`, placed in a track."""

    media_ref: MediaRef
    source_in: Fraction
    source_out: Fraction
    volume_db: float = 0.0
    muted: bool = False
    speed: Fraction = Fraction(1)
    fit_mode: FitMode = "black_bars"
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def duration(self) -> Fraction:
        return (self.source_out - self.source_in) / self.speed

    def __post_init__(self) -> None:
        if self.source_out <= self.source_in:
            raise ValueError("source_out must be after source_in")


@dataclass
class Gap:
    """Empty space in a track, left behind by a non-rippling delete."""

    duration: Fraction
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError("gap duration must be positive")


TrackItem = Clip | Gap


@dataclass
class Track:
    kind: TrackKind
    items: list[TrackItem] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def duration(self) -> Fraction:
        return sum((item.duration for item in self.items), Fraction(0))


@dataclass
class Project:
    frame_rate: Rate
    tracks: list[Track] = field(default_factory=list)
    name: str = "Untitled"
    schema_version: int = 2  # io/otio_io.py's SCHEMA_VERSION

    @property
    def duration(self) -> Fraction:
        return max((track.duration for track in self.tracks), default=Fraction(0))


def default_clip_for(media_ref: MediaRef) -> Clip:
    """A clip spanning the media's whole natural duration, or
    `STILL_IMAGE_DEFAULT_DURATION` for a still image (which has no natural
    duration of its own -- `MediaRef.duration` is just a large sentinel)."""
    if media_ref.is_still_image:
        return Clip(
            media_ref=media_ref, source_in=Fraction(0), source_out=STILL_IMAGE_DEFAULT_DURATION
        )
    return Clip(media_ref=media_ref, source_in=Fraction(0), source_out=media_ref.duration)


DEFAULT_RESOLUTION = (1280, 720)
"""Used before the project has any video clip (e.g. the export dialog's size
estimate on an empty project)."""

AUTO_MAX_SHORT_SIDE = 1080
""""Automatic" never goes above 1080p: one 4K clip shouldn't turn a class
video into a huge 4K export."""


def auto_resolution(
    project: Project,
    default: tuple[int, int] = DEFAULT_RESOLUTION,
    max_short_side: int = AUTO_MAX_SHORT_SIDE,
) -> tuple[int, int]:
    """The output size that suits this project's clips: export's
    "Automatic" choice, and the size the preview's gap filler uses.

    - **Shape:** whichever shape (aspect ratio, to one decimal place, so
      1920x1080 and 854x480 count as the same) covers the most timeline
      time -- mostly landscape gives landscape, an all-phone project stays
      vertical. A tie goes to the shape that appears first.
    - **Size:** the sharpest clip of that shape (largest short side; its
      exact ratio too), capped at `max_short_side`. Never larger than the
      clips themselves.

    Clips of other shapes are fitted into it by their Fit mode. Media sizes
    are display sizes (see media/probe.py), so rotated phone video counts
    as vertical.
    """
    sized = [
        (item, item.media_ref.width, item.media_ref.height)
        for track in project.tracks
        if track.kind == "video"
        for item in track.items
        if isinstance(item, Clip) and item.media_ref.width and item.media_ref.height
    ]
    if not sized:
        return default

    time_by_shape: dict[float, Fraction] = {}
    for clip, width, height in sized:
        shape = _shape(width, height)
        time_by_shape[shape] = time_by_shape.get(shape, Fraction(0)) + clip.duration
    # max() returns the first of equal maxima; dicts keep timeline order.
    winner = max(time_by_shape, key=time_by_shape.__getitem__)
    in_shape = [(c, w, h) for c, w, h in sized if _shape(w, h) == winner]

    _, ratio_w, ratio_h = max(
        in_shape, key=lambda entry: (min(entry[1], entry[2]), entry[0].duration)
    )
    short = min(ratio_w, ratio_h, max_short_side)
    if ratio_w >= ratio_h:
        return even(Fraction(short * ratio_w, ratio_h)), even(Fraction(short))
    return even(Fraction(short)), even(Fraction(short * ratio_h, ratio_w))


def even(value: Fraction) -> int:
    """Nearest even whole number (at least 2) -- H.264 with yuv420p needs
    even frame dimensions."""
    return max(2, round(value / 2) * 2)


def _shape(width: int, height: int) -> float:
    return round(width / height, 1)
