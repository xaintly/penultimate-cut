"""Draw the Microsoft Store package's icons from icon.png, into a folder.

    python scripts/make_store_assets.py <folder>

scripts/build_msix.ps1 runs this into the package it's building; nothing
here is kept in the repo. Windows looks for each logo by name, in several
sizes it picks between by screen scaling ("scale-200") or by the size it
needs ("targetsize-32"); "altform-unplated" ones are drawn as they are on
the taskbar and in Start, instead of on a coloured square. The package's
resources.pri (made by makepri) is what lists them all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QImage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import RESOURCES, _scaled, _squared, _trimmed  # noqa: E402

LOGOS = {
    "StoreLogo": 50,
    "Square44x44Logo": 44,
    "Square150x150Logo": 150,
}
SCALES = (100, 200)
TARGET_SIZES = (16, 24, 32, 48, 256)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window needed, e.g. in CI
    _app = QGuiApplication(sys.argv)  # QImage scaling needs one
    source = QImage(str(RESOURCES / "icon.png"))
    if source.isNull():
        sys.exit(f"Couldn't read {RESOURCES / 'icon.png'}")
    square = _squared(_trimmed(source.convertToFormat(QImage.Format.Format_ARGB32)))

    def save(name: str, size: int) -> None:
        if not _scaled(square, size).save(str(folder / name)):
            sys.exit(f"Couldn't write {folder / name}")

    for logo, size in LOGOS.items():
        for scale in SCALES:
            save(f"{logo}.scale-{scale}.png", size * scale // 100)
    for size in TARGET_SIZES:
        save(f"Square44x44Logo.targetsize-{size}.png", size)
        save(f"Square44x44Logo.targetsize-{size}_altform-unplated.png", size)
    print(f"Wrote the Store icons to {folder}")


if __name__ == "__main__":
    main()
