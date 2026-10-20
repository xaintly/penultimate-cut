"""Main window skeleton: preview (top), media bin (left), timeline (bottom).

Timeline is still a placeholder -- it lands as Phase 1 is built out further.
The media bin and preview widgets are real.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from penultimate_cut.ui.media_bin import MediaBin
from penultimate_cut.ui.preview_widget import PreviewWidget


def _placeholder(text: str) -> QFrame:
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(frame)
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    return frame


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Penultimate Cut")
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        self.media_bin = MediaBin()
        self.preview = PreviewWidget()

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.addWidget(self.media_bin)
        top_split.addWidget(self.preview)
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 3)

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(_placeholder("Timeline"))
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)

        root_layout.addWidget(main_split)

        import_button = QPushButton("Import")
        import_button.clicked.connect(self.media_bin.choose_files)

        button_row = QHBoxLayout()
        button_row.addWidget(import_button)
        for label in ("Split", "Delete", "Add Title", "Export"):
            button_row.addWidget(QPushButton(label))
        root_layout.addLayout(button_row)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.media_bin.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
