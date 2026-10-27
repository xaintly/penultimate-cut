"""Build penultimate_cut/resources/icon.ico from icon.png.

Run from the repo root after changing the artwork:

    python scripts/make_icon.py

Windows picks a different size of the icon for each place it shows it
(title bar, taskbar, Explorer, Start), so the .ico holds all of them, each
stored as PNG (fine everywhere since Windows Vista). Most online converters
write a single size. The artwork's transparent margin is trimmed first, so
the drawing fills the icon the way other apps' icons do.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

RESOURCES = Path(__file__).resolve().parents[1] / "penultimate_cut" / "resources"
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
MARGIN = 0.03
"""Transparent border kept around the trimmed artwork, as a share of the side."""


def main() -> None:
    _app = QGuiApplication(sys.argv)  # QImage scaling needs one
    source = QImage(str(RESOURCES / "icon.png"))
    if source.isNull():
        sys.exit(f"Couldn't read {RESOURCES / 'icon.png'}")
    square = _squared(_trimmed(source.convertToFormat(QImage.Format.Format_ARGB32)))
    pngs = [_png_bytes(_scaled(square, size)) for size in SIZES]
    (RESOURCES / "icon.ico").write_bytes(_ico(list(zip(SIZES, pngs, strict=True))))
    print(f"Wrote {RESOURCES / 'icon.ico'} ({', '.join(map(str, SIZES))} px)")


def _trimmed(image: QImage) -> QImage:
    """`image` cropped to its non-transparent pixels."""
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 8:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    if right < 0:
        sys.exit("icon.png is fully transparent")
    return image.copy(QRect(left, top, right - left + 1, bottom - top + 1))


def _squared(image: QImage) -> QImage:
    """`image` centered on a transparent square with a small margin."""
    side = round(max(image.width(), image.height()) * (1 + 2 * MARGIN))
    square = QImage(side, side, QImage.Format.Format_ARGB32)
    square.fill(Qt.GlobalColor.transparent)
    painter = QPainter(square)
    painter.drawImage((side - image.width()) // 2, (side - image.height()) // 2, image)
    painter.end()
    return square


def _scaled(image: QImage, size: int) -> QImage:
    """Halve repeatedly, then one last smooth step: a single big
    smooth-scale step averages too few source pixels and looks jagged."""
    while image.width() >= size * 2:
        image = image.scaled(
            image.width() // 2,
            image.height() // 2,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return image.scaled(
        size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def _ico(images: list[tuple[int, bytes]]) -> bytes:
    """ICONDIR header, one 16-byte entry per image, then the PNG data."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


if __name__ == "__main__":
    main()
