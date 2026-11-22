"""The Filter tool's side panel: the grid of looks and, under the chosen one, the Intensity slider.

It only edits a `FilterChoice` and announces it (`changed` while it moves, `released` when the gesture is
done, which is when the editor makes one undo step). The picture it affects is `adjust_view.AdjustView`;
the pixels are in `services.filters`.
"""

from __future__ import annotations

from dataclasses import replace

from PIL import Image, ImageOps
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...services.filters import FILTERS, INTENSITY_MAX, ORIGINAL, FilterChoice, apply_filter
from ..common.icons import draw_icon
from ..common.imageconv import pil_to_qimage
from ..common.motion import SmoothScrollArea
from ..viewer import style as vs

WIDTH = 380
COLUMNS = 3
THUMB = 94
SOURCE_SIDE = 188
CARD_HEIGHT = 136


def _style() -> str:
    return """
#FilterPanel { background: rgba(255,255,255,0.045); border-radius: 12px; }
#FilterPanel QScrollArea { background: transparent; border: none; }
#FilterContent { background: transparent; }
QLabel#FilterText { color: #f2f2f2; font-size: 14px; background: transparent; }
#FilterPanel QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px; }
#FilterPanel QScrollBar::handle:vertical { background: rgba(255,255,255,0.22); border-radius: 3px; min-height: 28px; }
#FilterPanel QScrollBar::add-line:vertical, #FilterPanel QScrollBar::sub-line:vertical { height: 0; }
#FilterPanel QScrollBar::add-page:vertical, #FilterPanel QScrollBar::sub-page:vertical { background: transparent; }
"""


class _FilterCard(QWidget):
    picked = Signal(str)

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self._key, self._label = key, label
        self._pixmap: QPixmap | None = None
        self._selected = False
        self._hover = False
        self.setFixedHeight(CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.picked.emit(self._key)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        tile = QRectF((self.width() - THUMB) / 2, 0, THUMB, THUMB)
        if self._selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 36))
            painter.drawRoundedRect(tile, 3, 3)
            draw_icon(painter, "check", QRectF(tile.center().x() - 13, tile.center().y() - 13, 26, 26), vs.ICON, stroke=1.6)
        elif self._pixmap is not None:
            painter.drawPixmap(tile, self._pixmap, QRectF(self._pixmap.rect()))
            if self._hover:
                painter.setPen(QColor(255, 255, 255, 110))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(tile.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setBold(self._selected)
        painter.setFont(font)
        painter.setPen(vs.TEXT)
        painter.drawText(
            QRectF(0, THUMB + 6, self.width(), CARD_HEIGHT - THUMB - 6),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            self._label,
        )
        painter.end()


class _IntensitySlider(QWidget):
    changed = Signal(int)
    released = Signal()
    PAD = 10
    RADIUS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = INTENSITY_MAX
        self._dragging = False
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._wheel_done = QTimer(self)
        self._wheel_done.setSingleShot(True)
        self._wheel_done.setInterval(350)
        self._wheel_done.timeout.connect(self.released.emit)
        self._accent = vs.accent_pair()[0]

    def set_value(self, value: int) -> None:
        value = max(0, min(INTENSITY_MAX, int(value)))
        if value != self._value:
            self._value = value
            self.update()

    def _x_of(self, value: float) -> float:
        return self.PAD + value / INTENSITY_MAX * (self.width() - 2 * self.PAD)

    def _value_at(self, x: float) -> int:
        span = max(1, self.width() - 2 * self.PAD)
        return max(0, min(INTENSITY_MAX, round((x - self.PAD) / span * INTENSITY_MAX)))

    def _move_to(self, value: int) -> None:
        value = max(0, min(INTENSITY_MAX, value))
        if value != self._value:
            self._value = value
            self.update()
            self.changed.emit(value)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 56))
        painter.drawRoundedRect(QRectF(self.PAD, y - 2, self.width() - 2 * self.PAD, 4), 2, 2)
        thumb = self._x_of(self._value)
        painter.setBrush(self._accent)
        painter.drawRoundedRect(QRectF(self.PAD, y - 2, max(0.0, thumb - self.PAD), 4), 2, 2)
        radius = self.RADIUS + (1 if self._dragging else 0)
        painter.setBrush(QColor("#f2f2f2"))
        painter.drawEllipse(QPointF(thumb, y), radius, radius)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._move_to(self._value_at(event.position().x()))
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.update()
            self.released.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        self._dragging = False
        self._move_to(INTENSITY_MAX)
        self.released.emit()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self._move_to(self._value + round(steps) * (5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1))
            self._wheel_done.start()
        event.accept()


class _IntensityRow(QWidget):
    changed = Signal(int)
    released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        title, self._number = QLabel("Intensity"), QLabel(str(INTENSITY_MAX))
        for label in (title, self._number):
            label.setObjectName("FilterText")
        self._number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._slider = _IntensitySlider()
        self._slider.changed.connect(self._on_changed)
        self._slider.released.connect(self.released)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._number)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 10, 0, 12)
        column.setSpacing(4)
        column.addLayout(header)
        column.addWidget(self._slider)

    def set_value(self, value: int) -> None:
        self._slider.set_value(value)
        self._number.setText(str(value))

    def _on_changed(self, value: int) -> None:
        self._number.setText(str(value))
        self.changed.emit(value)

# what if you kill someone?

class FilterPanel(QWidget):
    changed = Signal(object)
    released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FilterPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(_style())
        self._choice = FilterChoice()
        self._stretch_row = 0
        self._cards: dict[str, _FilterCard] = {}

        content = QWidget()
        content.setObjectName("FilterContent")
        self._grid = QGridLayout(content)
        self._grid.setContentsMargins(16, 16, 16, 16)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(0)
        for column in range(COLUMNS):
            self._grid.setColumnStretch(column, 1)
        for key, label in FILTERS:
            card = _FilterCard(key, label)
            card.picked.connect(self._on_pick)
            self._cards[key] = card
        self._row = _IntensityRow()
        self._row.changed.connect(self._on_intensity)
        self._row.released.connect(self.released)
        self._cards[self._choice.name].set_selected(True)
        self._arrange()

        scroll = SmoothScrollArea()  # the wheel glides, like in Settings
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def set_source(self, image: Image.Image) -> None:
        square = ImageOps.fit(image.convert("RGB"), (SOURCE_SIDE, SOURCE_SIDE), Image.Resampling.LANCZOS)
        for key, card in self._cards.items():
            card.set_pixmap(QPixmap.fromImage(pil_to_qimage(apply_filter(square, FilterChoice(key)))))

    def set_choice(self, choice: FilterChoice) -> None:
        if choice != self._choice:
            self._show(choice)

    def _show(self, choice: FilterChoice) -> None:
        self._choice = choice
        for key, card in self._cards.items():
            card.set_selected(key == choice.name)
        self._row.set_value(choice.intensity)
        self._arrange()

    def _arrange(self) -> None:
        for widget in (*self._cards.values(), self._row):
            self._grid.removeWidget(widget)
        keys = [key for key, _ in FILTERS]
        chosen_row = keys.index(self._choice.name) // COLUMNS if self._choice.name in keys else 0
        with_row = self._choice.name != ORIGINAL
        last = 0
        for index, key in enumerate(keys):
            row = index // COLUMNS
            if with_row and row > chosen_row:
                row += 1
            last = max(last, row)
            self._grid.addWidget(self._cards[key], row, index % COLUMNS)
        if with_row:
            self._grid.addWidget(self._row, chosen_row + 1, 0, 1, COLUMNS)
            self._row.show()
        else:
            self._row.hide()
        self._grid.setRowStretch(self._stretch_row, 0)
        self._stretch_row = last + 2
        self._grid.setRowStretch(self._stretch_row, 1)

    def _on_pick(self, key: str) -> None:
        if key == self._choice.name:
            return
        choice = FilterChoice(key, INTENSITY_MAX)
        self._show(choice)
        self.changed.emit(choice)
        self.released.emit()

    def _on_intensity(self, value: int) -> None:
        self._choice = replace(self._choice, intensity=value)
        self.changed.emit(self._choice)
