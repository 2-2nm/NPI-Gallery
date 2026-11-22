from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .metrics import DEFAULT_EXPANDED_WIDTH, HANDLE_WIDTH
from .nav_delegate import tone

if TYPE_CHECKING:
    from .panel import Sidebar


class SidebarResizeHandle(QWidget):
    """Draggable right-edge grip that lets the user stretch the sidebar width."""

    def __init__(self, sidebar: "Sidebar"):
        super().__init__(sidebar)
        self._sidebar = sidebar
        self._dragging = False
        self._hovered = False
        self._press_global_x = 0
        self._press_width = 0
        self.setFixedWidth(HANDLE_WIDTH)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setMouseTracking(True)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._press_global_x = round(event.globalPosition().x())
            self._press_width = self._sidebar.width()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            delta = round(event.globalPosition().x()) - self._press_global_x
            self._sidebar._apply_drag_width(self._press_width + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.update()
            self._sidebar._finish_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._sidebar.set_expanded_width(DEFAULT_EXPANDED_WIDTH, persist=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        if not (self._hovered or self._dragging):
            return
        painter = QPainter(self)
        color = tone("splitter_drag" if self._dragging else "splitter_idle")
        painter.fillRect(self.width() - 2, 10, 2, max(0, self.height() - 20), color)
