from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..theme import theme
from .layer import backdrop


class BackdropRoot(QWidget):
    """The window's central widget: paints the background picture (if any) behind the floating cards.

    Without a picture it fills the window color, because the window is translucent on Windows 11 and an unpainted
    pixel would show as black.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        layer = backdrop()
        layer.attach(self)
        layer.changed.connect(self.update)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if not backdrop().paint(painter, self, event.rect()):
            painter.fillRect(event.rect(), QColor(theme.palette().chrome))
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        backdrop().canvas_resized()