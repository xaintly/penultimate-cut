"""Entry point: creates the QApplication and shows the main window.

Puts vendor/ on PATH before anything below imports `mpv`: python-mpv's own
module-level DLL search (ctypes.util.find_library, falling back to a file
right next to its own mpv.py) never looks in our vendor/ folder on its
own -- this is python-mpv's own documented fix for shipping libmpv-2.dll
alongside an app. media/executables.py's locate() handles ffmpeg/ffprobe
discovery itself and needs no such PATH change; harmless no-op on Linux,
where shared libraries are found via ldconfig, not PATH.
"""

from __future__ import annotations

import os
import sys

from penultimate_cut.media.executables import vendor_dir

os.environ["PATH"] = str(vendor_dir()) + os.pathsep + os.environ.get("PATH", "")

from PySide6.QtWidgets import QApplication  # noqa: E402

from penultimate_cut.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Penultimate Cut")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
