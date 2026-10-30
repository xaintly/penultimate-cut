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

MAX_VOLUME = 200
"""Clip volume is a percentage of the original, 0-200."""

FADE_LENGTH = Fraction(1)
"""Fade in from / out to black: one second, shortened on short clips (see
`fade_length`). No setting for it -- one less thing to learn."""

TITLE_BACKGROUND = "#012456"
"""A title card's default background: the classic dark blue."""
TITLE_TEXT_COLOR = "#ffffff"
TITLE_DEFAULT_TEXT = "Your Title"
TITLE_DEFAULT_DURATION = Fraction(5)
TITLE_MAX_DURATION = Fraction(24 * 60 * 60)
"""Like a still image's: a title has no length of its own, so this only
bounds trimming."""


@dataclass(frozen=True)
class Title:
    """A full-screen title card: text centered on a colored background.
    Colors are "#rrggbb". Frozen (hashable), so a rendered picture of it can
    be looked up by the title itself."""

    text: str = TITLE_DEFAULT_TEXT
    background: str = TITLE_BACKGROUND
    text_color: str = TITLE_TEXT_COLOR


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
    title: Title | None = None
    """Set for a title card, which is generated rather than read from a file
    (its `path` means nothing). It behaves like a still image otherwise."""

    @property
    def is_still_image(self) -> bool:
        return self.has_video and self.frame_rate is None

    @property
    def is_title(self) -> bool:
        return self.title is not None

    @property
    def display_name(self) -> str:
        """The file name, or for a title "Title: " and its first line."""
        if self.title is None:
            return self.path.name
        first_line = next((line for line in self.title.text.splitlines() if line.strip()), "")
        return f"Title: {first_line.strip()}" if first_line else "Title"


@dataclass
class Clip:
    """A trimmed reference to a slice of a `MediaRef`, placed in a track."""

    media_ref: MediaRef
    source_in: Fraction
    source_out: Fraction
    volume: int = 100
    """Percent of the original loudness, 0 to `MAX_VOLUME`."""
    muted: bool = False
    """Silent, but keeps `volume` for when it's unmuted."""
    speed: Fraction = Fraction(1)
    fit_mode: FitMode = "black_bars"
    fade_in: bool = False
    """Fade in from black (picture and sound) over `fade_length`."""
    fade_out: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def duration(self) -> Fraction:
        return (self.source_out - self.source_in) / self.speed

    @property
    def gain(self) -> Fraction:
        """What the clip's sound is multiplied by: 0 when muted, 1 unchanged."""
        return Fraction(0) if self.muted else Fraction(self.volume, 100)

    @property
    def fade_length(self) -> Fraction:
        """`FADE_LENGTH`, or less on a clip too short for its fades not to
        overlap (half the clip each when it fades both ways)."""
        room = self.duration / 2 if self.fade_in and self.fade_out else self.duration
        return min(FADE_LENGTH, room)

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
    schema_version: int = 4  # io/otio_io.py's SCHEMA_VERSION

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


def title_media(title: Title) -> MediaRef:
    """The media reference behind a title card."""
    return MediaRef(
        path=Path(),
        relative_path=None,
        duration=TITLE_MAX_DURATION,
        frame_rate=None,
        has_video=True,
        has_audio=False,
        title=title,
    )


def new_title_clip(title: Title | None = None) -> Clip:
    """A title card as added by "Add Title": the default length, fading in
    from and out to black."""
    return Clip(
        media_ref=title_media(title or Title()),
        source_in=Fraction(0),
        source_out=TITLE_DEFAULT_DURATION,
        fade_in=True,
        fade_out=True,
    )


def project_titles(project: Project) -> set[Title]:
    """Every distinct title card on the timeline -- what has to be drawn
    before the project can be previewed or exported."""
    return {
        item.media_ref.title
        for track in project.tracks
        for item in track.items
        if isinstance(item, Clip) and item.media_ref.title is not None
    }


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
