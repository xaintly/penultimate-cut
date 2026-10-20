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
    schema_version: int = 1

    @property
    def duration(self) -> Fraction:
        return max((track.duration for track in self.tracks), default=Fraction(0))
