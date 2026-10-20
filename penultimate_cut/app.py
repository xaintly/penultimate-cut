"""Entry point: creates the QApplication and shows the main window."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from penultimate_cut.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Penultimate Cut")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
