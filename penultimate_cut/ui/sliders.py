"""Slider behavior shared by the preview and the clip properties panel."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QProxyStyle, QSlider, QStyle, QStyleHintReturn, QStyleOption, QWidget


class _ClickToJumpStyle(QProxyStyle):
    """A left click anywhere on a slider's line jumps straight there (and
    keeps dragging from there), instead of Qt's default of stepping toward
    the pointer a page at a time."""

    def styleHint(
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        if hint == QStyle.StyleHint.SH_Slider_AbsoluteSetButtons:
            return Qt.MouseButton.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


def click_to_jump(slider: QSlider) -> None:
    """Make a click on `slider`'s line jump straight to that point."""
    style = _ClickToJumpStyle()
    style.setParent(slider)  # a widget's style isn't owned by the widget
    slider.setStyle(style)
