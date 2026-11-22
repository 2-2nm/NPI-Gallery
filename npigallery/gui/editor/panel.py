"""The controls under the picture: the straighten dial, rotate / flip buttons and the shape presets."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QStackedLayout, QToolButton, QVBoxLayout, QWidget

from ...services.editing import MAX_ANGLE
from ..viewer import style as vs

# // (label, aspect w / h) - None = free, "original" = the picture's own shape
PRESETS: list[tuple[str, object]] = [
    ("Free", None),
    ("Original", "original"),
    ("Square", 1.0),
    ("9:16", 9 / 16),
    ("16:9", 16 / 9),
    ("16:10", 16 / 10),
    ("4:5", 4 / 5),
    ("5:4", 5 / 4),
    ("3:4", 3 / 4),
    ("4:3", 4 / 3),
    ("2:3", 2 / 3),
    ("3:2", 3 / 2),
    ("5:7", 5 / 7),
    ("7:5", 7 / 5),
    ("1:2", 1 / 2),
    ("2:1", 2 / 1),
]


def ratio_name(aspect: float | None, original: float) -> str:
    if aspect is None:
        return "Free"
    if abs(aspect - original) < 0.005:
        return "Original"
    for label, value in PRESETS:
        if isinstance(value, float) and abs(value - aspect) < 0.005:
            return label
    return "Custom"


class GlyphButton(QToolButton):
    """A flat round-cornered button with a small drawn icon (or a text glyph)."""

    def __init__(self, kind: str, tip: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 34)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(vs.PRESSED if self.isDown() else vs.HOVER)
            painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
        color = QColor(vs.ICON if self.isEnabled() else QColor(255, 255, 255, 70))
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.translate(self.width() / 2, self.height() / 2)
        kind = self._kind
        if kind in ("turn-left", "turn-right"):
            if kind == "turn-left":
                painter.scale(-1, 1)
            painter.drawArc(QRectF(-7, -7, 14, 14), 90 * 16, -270 * 16)  # clockwise from the top
            painter.drawLine(QPointF(-7, 0), QPointF(-10, 3.5))
            painter.drawLine(QPointF(-7, 0), QPointF(-3.8, 3.2))
        elif kind in ("flip-h", "flip-v"):
            if kind == "flip-v":
                painter.rotate(90)
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(0, -9), QPointF(0, 9))
            painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolygon([QPointF(-3, -6), QPointF(-9, 6), QPointF(-3, 6)])
            painter.drawPolygon([QPointF(3, -6), QPointF(9, 6), QPointF(3, 6)])
        elif kind == "back":
            painter.drawLine(QPointF(8, 0), QPointF(-8, 0))
            painter.drawLine(QPointF(-8, 0), QPointF(-2, -6))
            painter.drawLine(QPointF(-8, 0), QPointF(-2, 6))
        else:  # "undo" / "redo": a text glyph
            font = QFont(self.font())
            font.setPixelSize(20)
            painter.setFont(font)
            painter.resetTransform()
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "↶" if kind == "undo" else "↷")
        painter.end()


class AngleDial(QWidget):
    """Ruler of tick marks under a fixed marker; drag it sideways to straighten the picture."""

    changed = Signal(float)
    released = Signal()
    PX_PER_DEGREE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._press_x: float | None = None
        self._press_value = 0.0
        self.setFixedHeight(46)
        self.setMinimumWidth(420)
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        """Show `value` without announcing it (the picture is already there)."""
        value = max(-MAX_ANGLE, min(MAX_ANGLE, value))
        if abs(value - self._value) > 1e-9:
            self._value = value
            self.update()

    def _move_to(self, value: float) -> None:
        value = round(max(-MAX_ANGLE, min(MAX_ANGLE, value)), 1)
        if abs(value) < 0.25:
            value = 0.0
        if value != self._value:
            self._value = value
            self.update()
            self.changed.emit(value)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = self.width() / 2
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(vs.TEXT)
        painter.drawText(QRectF(0, 0, self.width(), 16), Qt.AlignmentFlag.AlignCenter, f"{self._value:g} °")
        base = 38
        reach = self.width() / 2
        for degree in range(-int(MAX_ANGLE), int(MAX_ANGLE) + 1):
            x = centre + (degree - self._value) * self.PX_PER_DEGREE
            distance = abs(x - centre)
            if distance > reach:
                continue
            fade = max(0.0, 1 - distance / reach)
            major = degree % 5 == 0
            color = QColor(255, 255, 255, int((190 if major else 110) * fade))
            painter.setPen(QPen(color, 2 if major else 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(x, base), QPointF(x, base - (13 if major else 5)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(vs.TEXT)
        painter.drawRoundedRect(QRectF(centre - 2.5, base - 17, 5, 22), 2.5, 2.5)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x, self._press_value = event.position().x(), self._value

    def mouseMoveEvent(self, event) -> None:
        if self._press_x is not None:
            self._move_to(self._press_value - (event.position().x() - self._press_x) / self.PX_PER_DEGREE)

    def mouseReleaseEvent(self, event) -> None:
        if self._press_x is not None:
            self._press_x = None
            self.released.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        self._move_to(0.0)
        self.released.emit()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self._move_to(round(self._value) + steps)
            self.released.emit()
        event.accept()


class RatioBar(QWidget):
    """One cell per shape preset: a small outline of the shape with its name under it."""

    chosen = Signal(object)  # None, "original" or a float
    CELL_W = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = 0
        self._hover = -1
        self.setFixedHeight(66)
        self.setFixedWidth(self.CELL_W * len(PRESETS))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_current(self, label: str) -> None:
        self._current = next((i for i, (name, _v) in enumerate(PRESETS) if name == label), -1)
        self.update()

    def _index_at(self, x: float) -> int:
        index = int(x // self.CELL_W)
        return index if 0 <= index < len(PRESETS) else -1

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(12)
        for index, (label, value) in enumerate(PRESETS):
            cell = QRectF(index * self.CELL_W, 0, self.CELL_W, self.height())
            if index == self._current or index == self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(vs.SOFT if index == self._current else vs.HOVER)
                painter.drawRoundedRect(cell.adjusted(4, 2, -4, -2), 8, 8)
            aspect = value if isinstance(value, float) else 1.4
            box_w, box_h = (20, 20 / aspect) if aspect >= 1 else (20 * aspect, 20)
            box = QRectF(0, 0, box_w, box_h)
            box.moveCenter(QPointF(cell.center().x(), 22))
            painter.setPen(QPen(vs.ICON, 1.6, Qt.PenStyle.DashLine if value is None else Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 2.5, 2.5)
            painter.setFont(font)
            painter.setPen(vs.TEXT if index == self._current else vs.TEXT_DIM)
            painter.drawText(QRectF(cell.left(), 40, cell.width(), 20), Qt.AlignmentFlag.AlignCenter, label)
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        index = self._index_at(event.position().x())
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            self._current = index
            self.update()
            self.chosen.emit(PRESETS[index][1])


class _ShapeButton(QToolButton):
    """The button between rotate and flip that shows the current shape and opens the presets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = "Free"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(112, 34)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Aspect ratio")

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(vs.PRESSED if self.isDown() else vs.HOVER)
            painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)
        text_w = QFontMetricsF(font).horizontalAdvance(self._label)
        icon_w, gap = 14, 9
        left = (self.width() - (icon_w + gap + text_w)) / 2  # icon + name as one group, centred in the button
        painter.setPen(QPen(vs.ICON, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(left, (self.height() - icon_w) / 2, icon_w, icon_w), 2.5, 2.5)
        painter.setPen(vs.TEXT)
        painter.drawText(
            QRectF(left + icon_w + gap, 0, text_w + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self._label
        )
        painter.end()

# Grand Canyon

class BottomBar(QWidget):
    """Straighten dial + rotate / flip row; clicking the shape button swaps in the preset list."""

    turn = Signal(bool)  # clockwise?
    flip = Signal(bool)  # horizontal?
    ratio = Signal(object)
    angle_changed = Signal(float)
    angle_released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dial = AngleDial()
        self.dial.changed.connect(self.angle_changed)
        self.dial.released.connect(self.angle_released)

        left, right = GlyphButton("turn-left", "Rotate left 90°"), GlyphButton("turn-right", "Rotate right 90°")
        flip_h, flip_v = GlyphButton("flip-h", "Flip horizontally"), GlyphButton("flip-v", "Flip vertically")
        left.clicked.connect(lambda: self.turn.emit(False))
        right.clicked.connect(lambda: self.turn.emit(True))
        flip_h.clicked.connect(lambda: self.flip.emit(True))
        flip_v.clicked.connect(lambda: self.flip.emit(False))
        self._shape = _ShapeButton()
        self._shape.clicked.connect(lambda: self._pages.setCurrentIndex(1))

        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(left)
        row.addWidget(right)
        row.addStretch(1)
        row.addWidget(self._shape)
        row.addStretch(1)
        row.addWidget(flip_h)
        row.addWidget(flip_v)
        rotate_page = QWidget()
        column = QVBoxLayout(rotate_page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addWidget(self.dial)
        column.addLayout(row)

        self.ratios = RatioBar()
        self.ratios.chosen.connect(self._on_ratio)
        ratio_page = QWidget()
        back = GlyphButton("back", "Back to rotate and flip")
        back.clicked.connect(lambda: self._pages.setCurrentIndex(0))
        centred = QHBoxLayout(ratio_page)
        centred.setContentsMargins(0, 0, 0, 0)
        centred.addWidget(back, 0, Qt.AlignmentFlag.AlignTop)
        centred.addStretch(1)
        centred.addWidget(self.ratios)
        centred.addStretch(1)
        centred.addSpacing(back.width())  # keeps the presets centred

        self._pages = QStackedLayout(self)
        self._pages.setContentsMargins(0, 0, 0, 0)
        self._pages.addWidget(rotate_page)
        self._pages.addWidget(ratio_page)
        self.setMinimumHeight(88)

    def _on_ratio(self, value) -> None:
        self.ratio.emit(value)  # the list stays open; the back button returns to rotate / flip

    def show_ratios(self) -> None:
        self._pages.setCurrentIndex(1)

    def set_angle(self, degrees: float) -> None:
        self.dial.set_value(degrees)

    def set_ratio_label(self, label: str) -> None:
        self._shape.set_label(label)
        self.ratios.set_current(label)

    def sizeHint(self) -> QSize:
        return QSize(640, 92)
