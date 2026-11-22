"""The category list on the left of the Settings page (Windows Settings style).

The selection pill and its accent bar are one shape that glides from the old category to the new
one, instead of jumping.
"""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QSizePolicy, QVBoxLayout, QWidget

from ..common.icons import draw_icon
from ..common.motion import decelerate, make_animation
from ..sidebar.metrics import HIGHLIGHT_DURATION
from ..theme import theme

ITEM_HEIGHT = 40
ITEM_SPACING = 2
ICON_SIZE = 18


class NavItem(QAbstractButton):
    """One category: icon + label. Only the hover fill is drawn here; the selection is the list's."""

    def __init__(self, key: str, glyph: str, text: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._glyph = glyph
        self.setText(text)
        self.setCheckable(True)
        self.setFixedHeight(ITEM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def sizeHint(self) -> QSize:
        return QSize(200, ITEM_HEIGHT)

    def paintEvent(self, event) -> None:
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() and not self.isChecked():
            fill = QColor(p.control)
            fill.setAlpha(110)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 2, 0, -2), 6, 6)
        icon_rect = QRectF(14, (self.height() - ICON_SIZE) / 2, ICON_SIZE, ICON_SIZE)
        draw_icon(painter, self._glyph, icon_rect, QColor(p.icon), stroke=1.6)
        painter.setPen(QColor(p.text))
        painter.setFont(self.font())
        text_rect = QRectF(44, 0, self.width() - 52, self.height())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )


class NavList(QWidget):
    """The items stacked top to bottom, with the gliding selection drawn behind them."""

    selected = Signal(str)  # a category was clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ITEM_SPACING)
        self._layout = layout
        self.items: dict[str, NavItem] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._current: str | None = None
        self._from = 0.0
        self._to = 0.0
        self._y = 0.0
        self._glide = make_animation(self, HIGHLIGHT_DURATION, decelerate(), 0.0, 1.0)
        self._glide.valueChanged.connect(self._on_glide)

    def add_item(self, key: str, glyph: str, text: str) -> NavItem:
        item = NavItem(key, glyph, text)
        item.clicked.connect(lambda _checked=False, k=key: self.selected.emit(k))
        self._group.addButton(item)
        self._layout.addWidget(item)
        self.items[key] = item
        return item

    @property
    def current(self) -> str | None:
        return self._current

    def set_current(self, key: str, animate: bool = True) -> None:
        """Select a category; the pill glides there when the list is on screen."""
        item = self.items[key]
        item.setChecked(True)
        previous = self._current
        self._current = key
        resting = self._resting_y()
        if animate and previous is not None and previous != key and self.isVisible():
            self._from = self._y if self._glide.isRunning() else self.items[previous].y()
            self._to = resting
            self._glide.stop()
            self._glide.start()
        else:
            self._glide.stop()
            self._y = resting
        self.update()

    def _resting_y(self) -> float:
        item = self.items.get(self._current) if self._current else None
        return float(item.y()) if item is not None else 0.0

    def _on_glide(self, progress: float) -> None:
        self._y = self._from + (self._to - self._from) * progress
        self.update()

    def paintEvent(self, event) -> None:
        if self._current is None:
            return
        # Not gliding: follow the item's real position, so resizes and first show are always right.
        y = self._y if self._glide.isRunning() else self._resting_y()
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = QRectF(0, y + 2, self.width(), ITEM_HEIGHT - 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.control))
        painter.drawRoundedRect(pill, 6, 6)
        painter.setBrush(QColor(p.accent))
        painter.drawRoundedRect(QRectF(0, pill.center().y() - 8, 3, 16), 1.5, 1.5)