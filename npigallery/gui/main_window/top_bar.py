from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QMainWindow, QToolButton, QWidget

from ..common.icons import draw_icon, icon
from ..theme import theme


PILL_HEIGHT = 46
TOP_BAR_HEIGHT = 66


class CaptionButton(QToolButton):
    def __init__(self, name: str, close: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("CaptionClose" if close else "Caption")
        self.setFixedSize(34, 32)
        self.setIconSize(QSize(14, 14))
        self.set_glyph(name)

    def set_glyph(self, name: str) -> None:
        self.setIcon(icon(name, "#f2f2f2", 14))


class CornerCloseButton(CaptionButton):
    """Close button whose hit area stretches to the window's top-right corner.

    The visible button keeps its 34x32 size, but the widget itself extends up and to the right
    edge of the window, so flinging the mouse into the corner still hits it (like native Windows).
    """

    VISUAL = QSize(34, 32)

    def __init__(self, parent=None):
        super().__init__("close", close=True, parent=parent)
        self.setObjectName("CaptionCornerClose")
        # CaptionButton pins itself to 34x32; this one must be free to stretch to the window corner.
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._visual_top = 0

    def place(self, visual_left: int, visual_top: int, right_edge: int) -> None:
        """Position so the visible part sits at (visual_left, visual_top) and the hit area reaches the corner."""
        self._visual_top = visual_top
        self.setGeometry(
            visual_left, 0, max(self.VISUAL.width(), right_edge - visual_left), visual_top + self.VISUAL.height()
        )
        self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        visual = QRectF(0, self._visual_top, self.VISUAL.width(), self.VISUAL.height())
        if self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#a52315"))
            painter.drawRoundedRect(visual, 16, 16)
        elif self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#c42b1c"))
            painter.drawRoundedRect(visual, 16, 16)
        size = self.iconSize()
        icon_rect = QRect(
            round(visual.center().x() - size.width() / 2),
            round(visual.center().y() - size.height() / 2),
            size.width(),
            size.height(),
        )
        self.icon().paint(painter, icon_rect)
        painter.end()


class DropdownToolButton(QToolButton):
    def __init__(self, icon_name: str, text: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Tool")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setIcon(icon(icon_name))
        self.setIconSize(QSize(18, 18))
        self.setText(text)
        self.setToolTip(tooltip)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width() + 16, hint.height())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_icon(
            painter,
            "chevron-down",
            QRectF(self.width() - 18, (self.height() - 10) / 2, 10, 10),
            theme.palette().icon,
            stroke=2.2,
        )
        painter.end()


class TopBar(QWidget):
    """Row of floating pills above the photos: title, search, toolbar and window buttons."""

    def __init__(self, search: QLineEdit, window: QMainWindow, title_pill: QWidget, toolbar_pill: QWidget):
        super().__init__(window)
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(TOP_BAR_HEIGHT)
        self._window = window
        self.title_pill = title_pill
        self.toolbar_pill = toolbar_pill

        # The search box is not part of the layout: it is centred on the bar by _place_search, so it
        # stays in the middle on the gallery page and on the settings page (where the toolbar is hidden).
        self._search = search
        search.setParent(self)
        search.setFixedHeight(PILL_HEIGHT)

        self.minimize_button = CaptionButton("min", parent=self)
        self.maximize_button = CaptionButton("max", parent=self)
        self.close_button = CornerCloseButton(parent=self)
        self.minimize_button.setToolTip("Minimize")
        self.close_button.setToolTip("Close")
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        self.sync_maximized()

        controls = QFrame(self)
        controls.setObjectName("Pill")
        controls.setProperty("dragRegion", True)
        controls.setFixedHeight(PILL_HEIGHT)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(4, 0, 4, 0)
        controls_layout.setSpacing(0)
        controls_layout.addWidget(self.minimize_button)
        controls_layout.addWidget(self.maximize_button)
        # Placeholder: the real close button floats above it and extends to the window corner.
        self._close_slot = QWidget(controls)
        self._close_slot.setFixedSize(CornerCloseButton.VISUAL)
        controls_layout.addWidget(self._close_slot)
        self._controls = controls

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 12, 12, 8)
        layout.setSpacing(10)
        layout.addWidget(title_pill)
        layout.addStretch(1)
        layout.addWidget(toolbar_pill)
        layout.addWidget(controls)

    def _place_close_button(self) -> None:
        box = self._controls.geometry()
        left = box.right() + 1 - 4 - CornerCloseButton.VISUAL.width()
        top = box.top() + (box.height() - CornerCloseButton.VISUAL.height()) // 2
        self.close_button.place(left, top, self.width())

    SEARCH_MAX_WIDTH = 280
    SEARCH_MIN_WIDTH = 140
    SEARCH_GAP = 10

    def _place_search(self) -> None:
        """Centre the search box on the *window* (not on the strip beside the sidebar), as wide as
        fits between its neighbours."""
        controls = self._controls.geometry()
        if controls.width() <= 0:
            return
        left = self.title_pill.geometry().right() + 1 + self.SEARCH_GAP
        right = (self.toolbar_pill if self.toolbar_pill.isVisible() else self._controls).geometry().left()
        right -= self.SEARCH_GAP
        centre = self.mapFrom(self._window, QPoint(self._window.width() // 2, 0)).x()
        half = min(centre - left, right - centre)
        width = int(max(self.SEARCH_MIN_WIDTH, min(self.SEARCH_MAX_WIDTH, 2 * half)))
        x = round(centre - width / 2)
        x = max(left, min(x, right - width))  # too cramped to centre: slide into the free space
        self._search.setGeometry(x, controls.top(), width, PILL_HEIGHT)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_close_button()
        self._place_search()

    def event(self, event) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self._place_search()  # a pill appeared, disappeared or changed size
        return handled

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_close_button()
        self._place_search()
        QTimer.singleShot(0, self._place_close_button)
        QTimer.singleShot(0, self._place_search)

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def sync_maximized(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.set_glyph("restore" if maximized else "max")
        self.maximize_button.setToolTip("Restore" if maximized else "Maximize")

    # Dragging and snapping are delegated to the OS so Aero snap / maximize-by-drag work.
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)