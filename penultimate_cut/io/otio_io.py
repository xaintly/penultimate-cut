"""Project <-> `.otio` file conversion.

Times are stored twice in the written file: once as OTIO `RationalTime`
(a float rate + value, for interop with other OTIO-reading apps) and once as
exact `Fraction` strings under `metadata["penultimate_cut"]` (so our own
round-trips never drift off frame boundaries, even for rates like 30000/1001
that floats can't represent exactly). On load, our metadata is preferred when
present; the OTIO fields are the fallback for files written by other tools.

Media is linked by **absolute path** and a **path relative to the project
file**, recomputed at save time. On load, the relative path is tried first,
then the absolute path; neither existing on disk is reported back as
`LoadResult.missing_media` for the caller (UI) to offer a relink flow for —
this module never prompts.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import opentimelineio as otio

from penultimate_cut.media.probe import ProbeError, probe
from penultimate_cut.model.project import Clip, Gap, MediaRef, Project, Track, TrackItem, TrackKind
from penultimate_cut.model.time import Rate, frame_to_seconds, seconds_to_frame

METADATA_KEY = "penultimate_cut"
SCHEMA_VERSION = 2
"""Version history (bump + add a migration in `_migrate` on every change):

1. Initial.
2. Media width/height are the *display* size (rotation and non-square
   pixels applied -- see media/probe.py), not the stored frame size.
"""

_ADAPTER_NAME = "otio_json"
"""Passed explicitly rather than left for OTIO to guess from the file's
extension: the autosave path is `<project>.otio.autosave`, whose actual
extension (`.autosave`) OTIO has no adapter for."""

_FALLBACK_RATE = Fraction(30)
"""Nominal rate used only to pick a `RationalTime` value for media with no frame rate
(audio-only, still images). Our own round-trips never depend on this value being
meaningful — they read the exact `Fraction` from metadata instead."""


@dataclass
class LoadResult:
    project: Project
    missing_media: list[MediaRef] = field(default_factory=list)


def save_project(project: Project, path: Path) -> None:
    timeline = _project_to_timeline(project, path)
    otio.adapters.write_to_file(timeline, str(path), adapter_name=_ADAPTER_NAME)


def load_project(path: Path) -> LoadResult:
    timeline = otio.adapters.read_from_file(str(path), adapter_name=_ADAPTER_NAME)
    return _timeline_to_project(timeline, path)


def find_relink_candidates(missing: list[MediaRef], search_folder: Path) -> dict[str, Path]:
    """For each `MediaRef` in `missing`, look for a file directly inside
    `search_folder` (not recursive) whose name matches the missing file's own
    name. Returns `{media_ref.id: found_path}` for every match found;
    `MediaRef`s with no match are simply absent from the result, left for the
    caller to still report as unresolved.

    Doesn't mutate anything -- relinking a `MediaRef` (setting its `.path`,
    which immediately applies to every `Clip` sharing that same object,
    since loading already deduplicates by id) is the caller's job. Relinking
    isn't an edit in the undo-history sense: there's nothing a user would
    want to undo about having pointed a broken reference at the right file.
    """
    by_name = {
        candidate.name: candidate for candidate in search_folder.iterdir() if candidate.is_file()
    }
    matches: dict[str, Path] = {}
    for media_ref in missing:
        found = by_name.get(media_ref.path.name)
        if found is not None:
            matches[media_ref.id] = found
    return matches


# --- time helpers ------------------------------------------------------------


def _to_rational_time(seconds: Fraction, rate: Rate) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds_to_frame(seconds, rate), float(rate))


def _from_rational_time(rt: otio.opentime.RationalTime, rate: Rate) -> Fraction:
    return frame_to_seconds(round(rt.value), rate)


def _resolve_media_path(
    target_url: str, relative_path: str | None, project_path: Path
) -> tuple[Path, bool]:
    candidates = []
    if relative_path:
        candidates.append((project_path.parent / relative_path).resolve())
    candidates.append(Path(target_url))
    for candidate in candidates:
        if candidate.exists():
            return candidate, True
    return candidates[0], False


# --- Project / Timeline -------------------------------------------------------


def _project_to_timeline(project: Project, path: Path) -> otio.schema.Timeline:
    timeline = otio.schema.Timeline(name=project.name)
    timeline.metadata[METADATA_KEY] = {
        "schema_version": SCHEMA_VERSION,
        "frame_rate": str(project.frame_rate),
    }
    for track in project.tracks:
        timeline.tracks.append(_track_to_otio(track, project.frame_rate, path))
    return timeline


def _timeline_to_project(timeline: otio.schema.Timeline, path: Path) -> LoadResult:
    meta = timeline.metadata.get(METADATA_KEY, {})
    frame_rate = Fraction(meta["frame_rate"]) if "frame_rate" in meta else _FALLBACK_RATE
    schema_version = meta.get("schema_version", SCHEMA_VERSION)

    project = Project(
        frame_rate=frame_rate, name=timeline.name or "Untitled", schema_version=schema_version
    )
    missing: dict[str, MediaRef] = {}
    media_cache: dict[str, tuple[MediaRef, bool]] = {}

    for otio_track in timeline.tracks:
        track = _track_from_otio(otio_track, frame_rate, path, media_cache)
        project.tracks.append(track)

    for media_ref, found in media_cache.values():
        if not found:
            missing[media_ref.id] = media_ref

    _migrate(schema_version, media_cache.values())
    project.schema_version = SCHEMA_VERSION

    return LoadResult(project=project, missing_media=list(missing.values()))


def _migrate(schema_version: int, media: Iterable[tuple[MediaRef, bool]]) -> None:
    if schema_version < 2:
        # Re-measure video media at its display size. Media that's missing
        # (or no longer probes) keeps its old, possibly sideways, size.
        for media_ref, found in media:
            if found and media_ref.has_video:
                try:
                    info = probe(media_ref.path)
                except ProbeError:
                    continue
                media_ref.width, media_ref.height = info.width, info.height


# --- Track / items -------------------------------------------------------------


def _track_to_otio(track: Track, project_rate: Rate, path: Path) -> otio.schema.Track:
    kind = otio.schema.TrackKind.Video if track.kind == "video" else otio.schema.TrackKind.Audio
    otio_track = otio.schema.Track(name=track.kind, kind=kind)
    for item in track.items:
        otio_track.append(_item_to_otio(item, project_rate, path))
    return otio_track


def _track_from_otio(
    otio_track: otio.schema.Track,
    project_rate: Rate,
    path: Path,
    media_cache: dict[str, tuple[MediaRef, bool]],
) -> Track:
    kind: TrackKind = "video" if otio_track.kind == otio.schema.TrackKind.Video else "audio"
    items: list[TrackItem] = [
        _item_from_otio(child, project_rate, path, media_cache) for child in otio_track
    ]
    return Track(kind=kind, items=items)


def _item_to_otio(
    item: TrackItem, project_rate: Rate, path: Path
) -> otio.schema.Clip | otio.schema.Gap:
    if isinstance(item, Gap):
        return _gap_to_otio(item, project_rate)
    return _clip_to_otio(item, path)


def _item_from_otio(
    otio_item: otio.schema.Clip | otio.schema.Gap,
    project_rate: Rate,
    path: Path,
    media_cache: dict[str, tuple[MediaRef, bool]],
) -> TrackItem:
    if isinstance(otio_item, otio.schema.Gap):
        return _gap_from_otio(otio_item, project_rate)
    return _clip_from_otio(otio_item, path, media_cache)


# --- Gap ------------------------------------------------------------------------


def _gap_to_otio(gap: Gap, project_rate: Rate) -> otio.schema.Gap:
    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, float(project_rate)),
        duration=_to_rational_time(gap.duration, project_rate),
    )
    otio_gap = otio.schema.Gap(source_range=source_range)
    otio_gap.metadata[METADATA_KEY] = {"duration": str(gap.duration)}
    return otio_gap


def _gap_from_otio(otio_gap: otio.schema.Gap, project_rate: Rate) -> Gap:
    meta = otio_gap.metadata.get(METADATA_KEY, {})
    if "duration" in meta:
        duration = Fraction(meta["duration"])
    else:
        duration = _from_rational_time(otio_gap.source_range.duration, project_rate)
    return Gap(duration=duration)


# --- Clip -----------------------------------------------------------------------


def _clip_to_otio(clip: Clip, path: Path) -> otio.schema.Clip:
    media_reference = _media_ref_to_otio(clip.media_ref, path)
    rate = clip.media_ref.frame_rate or _FALLBACK_RATE
    source_range = otio.opentime.TimeRange(
        start_time=_to_rational_time(clip.source_in, rate),
        duration=_to_rational_time(clip.source_out - clip.source_in, rate),
    )
    otio_clip = otio.schema.Clip(
        name=clip.media_ref.path.name,
        media_reference=media_reference,
        source_range=source_range,
    )
    otio_clip.metadata[METADATA_KEY] = {
        "clip_id": clip.id,
        "source_in": str(clip.source_in),
        "source_out": str(clip.source_out),
        "volume_db": clip.volume_db,
        "muted": clip.muted,
        "speed": str(clip.speed),
        "fit_mode": clip.fit_mode,
    }
    return otio_clip


def _clip_from_otio(
    otio_clip: otio.schema.Clip, path: Path, media_cache: dict[str, tuple[MediaRef, bool]]
) -> Clip:
    media_ref, _found = _media_ref_from_otio(otio_clip.media_reference, path, media_cache)
    meta = otio_clip.metadata.get(METADATA_KEY, {})
    rate = media_ref.frame_rate or _FALLBACK_RATE

    if "source_in" in meta and "source_out" in meta:
        source_in = Fraction(meta["source_in"])
        source_out = Fraction(meta["source_out"])
    else:
        source_in = _from_rational_time(otio_clip.source_range.start_time, rate)
        source_out = source_in + _from_rational_time(otio_clip.source_range.duration, rate)

    return Clip(
        media_ref=media_ref,
        source_in=source_in,
        source_out=source_out,
        volume_db=meta.get("volume_db", 0.0),
        muted=meta.get("muted", False),
        speed=Fraction(meta["speed"]) if "speed" in meta else Fraction(1),
        fit_mode=meta.get("fit_mode", "black_bars"),  # type: ignore[arg-type]
        id=meta.get("clip_id") or uuid4().hex,
    )


# --- MediaRef -------------------------------------------------------------------


def _media_ref_to_otio(media_ref: MediaRef, path: Path) -> otio.schema.ExternalReference:
    relative_path = _relative_to(media_ref.path, path)
    ref = otio.schema.ExternalReference(target_url=str(media_ref.path))
    ref.metadata[METADATA_KEY] = {
        "media_id": media_ref.id,
        "relative_path": relative_path,
        "has_video": media_ref.has_video,
        "has_audio": media_ref.has_audio,
        "width": media_ref.width,
        "height": media_ref.height,
        "frame_rate": str(media_ref.frame_rate) if media_ref.frame_rate is not None else None,
        "duration": str(media_ref.duration),
    }
    rate = media_ref.frame_rate or _FALLBACK_RATE
    ref.available_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, float(rate)),
        duration=_to_rational_time(media_ref.duration, rate),
    )
    return ref


def _media_ref_from_otio(
    ref: otio.schema.ExternalReference, path: Path, cache: dict[str, tuple[MediaRef, bool]]
) -> tuple[MediaRef, bool]:
    meta = ref.metadata.get(METADATA_KEY, {})
    media_id = meta.get("media_id")
    cache_key = media_id or ref.target_url
    if cache_key in cache:
        return cache[cache_key]

    relative_path = meta.get("relative_path")
    resolved_path, found = _resolve_media_path(ref.target_url, relative_path, path)

    frame_rate: Rate | None
    if "frame_rate" in meta:
        frame_rate = Fraction(meta["frame_rate"]) if meta["frame_rate"] is not None else None
    elif ref.available_range is not None:
        frame_rate = Fraction(ref.available_range.start_time.rate).limit_denominator(1_000_000)
    else:
        frame_rate = None

    if "duration" in meta:
        duration = Fraction(meta["duration"])
    elif ref.available_range is not None:
        duration = _from_rational_time(ref.available_range.duration, frame_rate or _FALLBACK_RATE)
    else:
        duration = Fraction(0)

    media_ref = MediaRef(
        path=resolved_path,
        relative_path=relative_path,
        duration=duration,
        frame_rate=frame_rate,
        has_video=meta.get("has_video", True),
        has_audio=meta.get("has_audio", False),
        width=meta.get("width"),
        height=meta.get("height"),
        id=media_id or uuid4().hex,
    )
    cache[cache_key] = (media_ref, found)
    return media_ref, found


def _relative_to(target: Path, project_path: Path) -> str | None:
    try:
        return os.path.relpath(target, start=project_path.parent)
    except ValueError:
        return None  # e.g. different drives on Windows
