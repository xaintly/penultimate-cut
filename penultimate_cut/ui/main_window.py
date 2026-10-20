"""Main window skeleton: preview (top), media bin (left), timeline (bottom).

Placeholder widgets for now — real preview/media-bin/timeline widgets land as
Phase 1 is built out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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

        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.addWidget(_placeholder("Media bin"))
        top_split.addWidget(_placeholder("Preview"))
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 3)

        main_split = QSplitter(Qt.Orientation.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(_placeholder("Timeline"))
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)

        root_layout.addWidget(main_split)

        button_row = QHBoxLayout()
        for label in ("Import", "Split", "Delete", "Add Title", "Export"):
            button_row.addWidget(QPushButton(label))
        root_layout.addLayout(button_row)
