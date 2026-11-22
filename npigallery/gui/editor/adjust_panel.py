"""The Adjustment tool's side panel: the Light and Color sliders.

It only edits an `Adjustments` value and announces it (`changed` while a slider moves, `released` when
the gesture is done, which is when the editor makes one undo step). The picture it affects is
`adjust_view.AdjustView`; the pixels are in `services.adjust`.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ...services.adjust import AMOUNT, Adjustments
from ..common.icons import draw_icon
from ..common.motion import SmoothScrollArea
from ..viewer import style as vs

WIDTH = 380

# // (title, [(field of Adjustments, label, icon)])
GROUPS = (
    (
        "Light",
        (
            ("brightness", "Brightness", "adjust"),
            ("exposure", "Exposure", "adj-exposure"),
            ("contrast", "Contrast", "adj-contrast"),
            ("highlights", "Highlights", "adj-highlights"),
            ("shadows", "Shadows", "adj-shadows"),
            ("vignette", "Vignette", "adj-vignette"),
        ),
    ),
    (
        "Color",
        (
            ("saturation", "Saturation", "adj-saturation"),
            ("warmth", "Warmth", "adj-warmth"),
            ("tint", "Tint", "adj-tint"),
            ("sharpness", "Sharpness", "adj-sharpness"),
        ),
    ),
)


def _style() -> str:
    return """
#AdjustPanel { background: rgba(255,255,255,0.045); border-radius: 12px; }
#AdjustPanel QScrollArea { background: transparent; border: none; }
#AdjustContent { background: transparent; }
QLabel#AdjustTitle { color: #f2f2f2; font-size: 17px; font-weight: 600; background: transparent; }
#AdjustPanel QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px; }
#AdjustPanel QScrollBar::handle:vertical { background: rgba(255,255,255,0.22); border-radius: 3px; min-height: 28px; }
#AdjustPanel QScrollBar::add-line:vertical, #AdjustPanel QScrollBar::sub-line:vertical { height: 0; }
#AdjustPanel QScrollBar::add-page:vertical, #AdjustPanel QScrollBar::sub-page:vertical { background: transparent; }
"""


class AdjustSlider(QWidget):
    """A track from -100 to 100 with a round thumb; the coloured part runs from the middle to the thumb.

    Drag or click to set it, wheel to nudge it, double-click to put it back to 0.
    """

    changed = Signal(int)
    released = Signal()
    PAD = 10  # room for the thumb at both ends
    RADIUS = 8
    MAGNET = 2  # a drag this close to 0 lands on 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._dragging = False
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keeps Ctrl+Z / Esc going to the editor
        self._wheel_done = QTimer(self)
        self._wheel_done.setSingleShot(True)
        self._wheel_done.setInterval(350)  # a run of wheel steps is one undo step
        self._wheel_done.timeout.connect(self.released.emit)
        self._accent = vs.accent_pair()[0]

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        """Show `value` without announcing it (the picture is already there)."""
        value = max(-AMOUNT, min(AMOUNT, int(value)))
        if value != self._value:
            self._value = value
            self.update()

    def _x_of(self, value: float) -> float:
        left, right = self.PAD, self.width() - self.PAD
        return left + (value + AMOUNT) / (2 * AMOUNT) * (right - left)

    def _value_at(self, x: float) -> int:
        left, right = self.PAD, self.width() - self.PAD
        value = round((x - left) / max(1, right - left) * 2 * AMOUNT - AMOUNT)
        return max(-AMOUNT, min(AMOUNT, value))

    def _move_to(self, value: int) -> None:
        value = max(-AMOUNT, min(AMOUNT, value))
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
        zero, thumb = self._x_of(0), self._x_of(self._value)
        if self._value:
            painter.setBrush(self._accent)
            painter.drawRoundedRect(QRectF(min(zero, thumb), y - 2, abs(thumb - zero), 4), 2, 2)
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
            value = self._value_at(event.position().x())
            self._move_to(0 if abs(value) <= self.MAGNET else value)
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.update()
            self.released.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        self._dragging = False
        self._move_to(0)
        self.released.emit()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self._move_to(self._value + round(steps) * (5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1))
            self._wheel_done.start()
        event.accept()

# maybe someone will found it

class _RowHeader(QWidget):
    """Icon and name on the left, the number on the right."""

    def __init__(self, label: str, icon: str, parent=None):
        super().__init__(parent)
        self._label, self._icon, self._value = label, icon, 0
        self.setFixedHeight(24)

    def set_value(self, value: int) -> None:
        if value != self._value:
            self._value = value
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_icon(painter, self._icon, QRectF(0, (self.height() - 18) / 2, 18, 18), vs.ICON, stroke=1.7)
        font = QFont(self.font())
        font.setPixelSize(14)
        painter.setFont(font)
        painter.setPen(vs.TEXT)
        painter.drawText(QRectF(28, 0, self.width() - 28, self.height()), Qt.AlignmentFlag.AlignVCenter, self._label)
        painter.drawText(
            QRectF(0, 0, self.width(), self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            str(self._value),
        )
        painter.end()


class AdjustPanel(QWidget):
    changed = Signal(object)  # the new Adjustments, while a slider moves
    released = Signal()  # a slider gesture is finished: one undo step

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AdjustPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(_style())
        self._adjust = Adjustments()
        self._rows: dict[str, tuple[_RowHeader, AdjustSlider]] = {}

        content = QWidget()
        content.setObjectName("AdjustContent")
        column = QVBoxLayout(content)
        column.setContentsMargins(16, 16, 16, 16)
        column.setSpacing(0)
        for index, (title, rows) in enumerate(GROUPS):
            heading = QLabel(title)
            heading.setObjectName("AdjustTitle")
            if index:
                column.addSpacing(30)
            column.addWidget(heading)
            column.addSpacing(10)
            for field, label, icon in rows:
                header, slider = _RowHeader(label, icon), AdjustSlider()
                slider.changed.connect(lambda value, name=field: self._on_slider(name, value))
                slider.released.connect(self.released)
                column.addWidget(header)
                column.addWidget(slider)
                column.addSpacing(12)
                self._rows[field] = (header, slider)
        column.addStretch(1)

        scroll = SmoothScrollArea()  # the wheel glides, like in Settings
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_slider(self, field: str, value: int) -> None:
        self._rows[field][0].set_value(value)
        self._adjust = replace(self._adjust, **{field: value})
        self.changed.emit(self._adjust)

    def set_values(self, adjust: Adjustments) -> None:
        """Show `adjust` (after undo / redo / reset) without announcing it."""
        self._adjust = adjust
        for field, (header, slider) in self._rows.items():
            value = getattr(adjust, field)
            slider.set_value(value)
            header.set_value(value)