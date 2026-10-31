"""Pure functions on the media bin (`Project.media`): add, move, remove.

Like model/timeline_ops.py, each takes the list and returns a *new* one,
never mutating its argument, so commands can keep before/after snapshots.
"""

from __future__ import annotations

from penultimate_cut.model.project import Clip, MediaRef, Project


class MediaOpError(ValueError):
    """Raised when an op can't be applied (an index out of range)."""


def add_media(media: list[MediaRef], new: list[MediaRef]) -> list[MediaRef]:
    """`new` appended at the end, skipping any already in the bin (by id)."""
    present = {media_ref.id for media_ref in media}
    added = []
    for media_ref in new:
        if media_ref.id not in present:
            present.add(media_ref.id)
            added.append(media_ref)
    return [*media, *added]


def move_media(media: list[MediaRef], from_index: int, before_index: int) -> list[MediaRef]:
    """The entry at `from_index` moved to just before whatever is at
    `before_index` now (`len(media)` for the end), the way a dragged item
    lands between two others."""
    if not 0 <= from_index < len(media) or not 0 <= before_index <= len(media):
        raise MediaOpError(f"can't move entry {from_index} before {before_index}")
    moved = list(media)
    media_ref = moved.pop(from_index)
    moved.insert(before_index - 1 if before_index > from_index else before_index, media_ref)
    return moved


def remove_media(media: list[MediaRef], media_id: str) -> list[MediaRef]:
    return [media_ref for media_ref in media if media_ref.id != media_id]


def timeline_media(project: Project) -> list[MediaRef]:
    """Every file used on the timeline, in order of first use. Titles aren't
    files, so they're left out."""
    seen: set[str] = set()
    found: list[MediaRef] = []
    for track in project.tracks:
        for item in track.items:
            if isinstance(item, Clip) and not item.media_ref.is_title:
                if item.media_ref.id not in seen:
                    seen.add(item.media_ref.id)
                    found.append(item.media_ref)
    return found
