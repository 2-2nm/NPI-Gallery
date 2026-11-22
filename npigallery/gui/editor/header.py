"""The editor's own title bar: the photo's name on the left, minimize / maximize on the right (the close
button is `common.corner_close.CornerClose`, which the editor floats over the slot kept free for it)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ..viewer import style as vs
from ..viewer.overlays import IconButton

HEIGHT = 40
BUTTON = 36


class HeaderBar(QWidget):
    minimize_clicked = Signal()
    maximize_clicked = Signal()

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = name
        self.setFixedHeight(HEIGHT)
        self._min = IconButton("min", BUTTON, 16, self)
        self._max = IconButton("max", BUTTON, 16, self)
        # The close button is the editor's `CornerClose`, which floats over this slot and reaches the corner.
        self._close_slot = QWidget(self)
        self._close_slot.setFixedSize(BUTTON, BUTTON)
        self._min.setToolTip("Minimize")
        self._max.setToolTip("Maximize")
        self._min.clicked.connect(self.minimize_clicked)
        self._max.clicked.connect(self.maximize_clicked)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addStretch(1)
        for button in (self._min, self._max, self._close_slot):
            row.addWidget(button)

    def set_maximized(self, maximized: bool) -> None:
        self._max.set_icon("restore" if maximized else "max")
        self._max.setToolTip("Restore" if maximized else "Maximize")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(vs.TEXT)
        room = max(0, self.width() - 3 * (BUTTON + 2) - 24)
        text = QFontMetrics(font).elidedText(self._name, Qt.TextElideMode.ElideMiddle, room)
        painter.drawText(QRectF(8, 0, room + 8, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()

    def mousePressEvent(self, event) -> None:  # the empty part of the bar moves the window
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_clicked.emit()