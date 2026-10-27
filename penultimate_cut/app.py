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

import ctypes
import os
import sys

from penultimate_cut.media.executables import resources_dir, vendor_dir

os.environ["PATH"] = str(vendor_dir()) + os.pathsep + os.environ.get("PATH", "")

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from penultimate_cut.ui.main_window import MainWindow  # noqa: E402

ICON_PATH = resources_dir() / "icon.ico"
"""Built from icon.png by scripts/make_icon.py."""
_APP_USER_MODEL_ID = "PenultimateCut.PenultimateCut"


def main() -> int:
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        # Without its own ID, a run from source is grouped on the taskbar
        # under python.exe and shows Python's icon instead of ours. Not in
        # the installed app: there the .exe is already its own taskbar
        # entry, and an ID its Start-menu shortcut lacks would split a
        # pinned icon from the running window.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    app = QApplication(sys.argv)
    app.setApplicationName("Penultimate Cut")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
