"""Image lists that let the preview play pictures (photos and title cards).

mpv shows a plain picture as a single frame, which breaks seeking into it
and fades on it (see `preview/edl.py`). Instead the preview plays an image
list (`mf://@list`): a text file naming the picture once per frame, at
`edl.PICTURE_FPS`, for as long as its longest clip. Lists are kept in the
cache, named by the picture and length, and only ever written once.

mpv picks the picture's decoder from its file extension, so only the
formats in `IMAGE_LIST_SUFFIXES` (checked against a live libmpv) get a
list; anything else still plays as a plain picture.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path

from penultimate_cut.media.cache import cache_root
from penultimate_cut.model.project import Clip, Project, Title
from penultimate_cut.preview.edl import PICTURE_FPS

IMAGE_LIST_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"})
_STEP_SECONDS = 10
"""Lists come in steps of this many seconds, so trimming a picture a little
doesn't need a new one."""


def picture_lists(
    project: Project, title_pictures: Mapping[Title, Path] | None = None
) -> dict[str, Path]:
    """An image list for every picture on the timeline, by media id -- what
    `edl.to_edl` needs. `title_pictures` holds each title card's drawn
    picture (see `ui/titles.py`)."""
    longest: dict[str, tuple[Path, Fraction]] = {}
    for track in project.tracks:
        for item in track.items:
            if not isinstance(item, Clip) or not item.media_ref.is_still_image:
                continue
            media = item.media_ref
            if media.title is not None:
                if title_pictures is None or media.title not in title_pictures:
                    continue
                picture = title_pictures[media.title]
            else:
                picture = media.path
            if picture.suffix.lower() not in IMAGE_LIST_SUFFIXES:
                continue
            _, seconds = longest.get(media.id, (picture, Fraction(0)))
            longest[media.id] = (picture, max(seconds, item.duration))
    return {
        media_id: image_list(picture, seconds) for media_id, (picture, seconds) in longest.items()
    }


def image_list(picture: Path, seconds: Fraction) -> Path:
    """An image list repeating `picture` for at least `seconds`."""
    steps = max(1, math.ceil(seconds / _STEP_SECONDS))
    frames = steps * _STEP_SECONDS * PICTURE_FPS + 1
    digest = hashlib.sha1(str(picture.resolve()).encode("utf-8")).hexdigest()
    folder = cache_root() / "pictures"
    path = folder / f"{digest}-{frames}.txt"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        # Written under another name first: a half-written list must never
        # pass for a finished one.
        partial = path.with_suffix(".partial")
        partial.write_text(f"{picture}\n" * frames, encoding="utf-8")
        partial.replace(path)
    return path
