"""Drawing title cards.

A title is drawn once as a picture the size of the video, then previewed
and exported exactly like a still image, so what plays in the preview is
what ends up in the file. Qt draws the text: real Windows fonts, word
wrap, and a soft shadow, without ffmpeg's text filter and its escaping.

The text is as large as fits: about a ninth of the picture's short side,
shrunk until every line fits with a margin around it. The background is
the chosen color, a little lighter at the top and darker at the bottom.

Pictures are kept on disk, named by the title and size, so an unchanged
title is never drawn twice. Needs a running QGuiApplication.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter

from penultimate_cut.media.cache import cache_root
from penultimate_cut.model.project import Project, Title, project_titles

FONT_FAMILY = "Segoe UI"
"""Windows' own interface font; Qt substitutes a similar one elsewhere."""
_TEXT_SHARE = 0.11
"""Starting text height, as a share of the picture's short side."""
_MARGIN_SHARE = 0.08
"""Space kept clear on every side, as a share of the width/height."""
_SHADE = 115
"""How much lighter the top / darker the bottom of the background is (%)."""


def paint_title(painter: QPainter, rect: QRectF, title: Title) -> None:
    """Draw `title` filling `rect`."""
    background = QColor(title.background)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, background.lighter(_SHADE))
    gradient.setColorAt(1, background.darker(_SHADE))
    painter.save()
    painter.fillRect(rect, gradient)

    text = title.text.strip()
    if text:
        margin_x, margin_y = rect.width() * _MARGIN_SHARE, rect.height() * _MARGIN_SHARE
        area = rect.adjusted(margin_x, margin_y, -margin_x, -margin_y)
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        font = _fitted_font(text, area, flags, min(rect.width(), rect.height()))
        painter.setFont(font)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        offset = max(1.0, font.pixelSize() / 18)
        painter.setPen(QColor(0, 0, 0, 150))
        painter.drawText(area.translated(offset, offset), int(flags), text)
        painter.setPen(QColor(title.text_color))
        painter.drawText(area, int(flags), text)
    painter.restore()


def _fitted_font(text: str, area: QRectF, flags: Qt.AlignmentFlag, short_side: float) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setWeight(QFont.Weight.DemiBold)
    size = max(1, round(short_side * _TEXT_SHARE))
    while True:
        font.setPixelSize(size)
        bounds = QFontMetricsF(font).boundingRect(area, int(flags), text)
        fits = bounds.width() <= area.width() + 0.5 and bounds.height() <= area.height() + 0.5
        if fits or size <= 4:
            return font
        size = max(4, int(size * 0.9))


def title_image(title: Title, width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    paint_title(painter, QRectF(0, 0, width, height), title)
    painter.end()
    return image


def title_image_file(title: Title, width: int, height: int) -> Path:
    """A PNG of `title` at `width`x`height`, drawn if it isn't on disk yet."""
    key = json.dumps([title.text, title.background, title.text_color, width, height, FONT_FAMILY])
    name = hashlib.sha1(key.encode("utf-8")).hexdigest() + ".png"
    folder = cache_root() / "titles"
    path = folder / name
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        # Written under another name first: a half-written file must never
        # pass for a finished one.
        partial = path.with_suffix(".partial.png")
        if not title_image(title, width, height).save(str(partial), "PNG"):
            raise OSError(f"couldn't write the title picture {partial}")
        partial.replace(path)
    return path


def title_images(project: Project, width: int, height: int) -> dict[Title, Path]:
    """The picture of every title on the timeline, at `width`x`height` --
    what `export.filtergraph.build_filtergraph` and
    `preview.pictures.picture_lists` need."""
    return {title: title_image_file(title, width, height) for title in project_titles(project)}
