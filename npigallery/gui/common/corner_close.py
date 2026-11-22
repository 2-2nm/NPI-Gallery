"""A window's close button whose hit area runs on to the window's top-right corner.

People fling the mouse into the top-right corner of the screen to close a window, so the pixels around
the visible round button count as the button too (like the main window's `CornerCloseButton` and like
native Windows). The widget is a child of the window itself, not of the panel that holds the other caption
buttons, so it is free to stretch past that panel to the window's edge.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton

from .icons import draw_icon

HOVER = QColor("#c42b1c")
ICON = QColor("#f2f2f2")


class CornerClose(QAbstractButton):
    def __init__(self, parent, size: int = 36, glyph: int = 16):
        super().__init__(parent)
        self._size, self._glyph = size, glyph
        self._visual = QRectF(0, 0, size, size)
        self.setToolTip("Close")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def place(self, left: int, top: int, right_edge: int) -> None:
        """The round button is drawn with its top-left at (left, top); the widget reaches up to the top
        of the window and across to `right_edge` (the window's width)."""
        self._visual = QRectF(0, top, self._size, self._size)
        self.setGeometry(left, 0, max(self._size, right_edge - left), top + self._size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isDown() or self.underMouse():  # anywhere on the stretched hit area, as on the main window
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(HOVER.darker(115) if self.isDown() else HOVER)
            painter.drawEllipse(self._visual.adjusted(0.5, 0.5, -0.5, -0.5))
        side = float(self._glyph)
        glyph = QRectF(0, 0, side, side)
        glyph.moveCenter(self._visual.center())
        draw_icon(painter, "close", glyph, ICON, stroke=1.9)
        painter.end()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()