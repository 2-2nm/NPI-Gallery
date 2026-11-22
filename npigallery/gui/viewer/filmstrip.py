"""Thumbnail strip along the bottom: custom painted, so 4,000+ photos cost nothing."""

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ...services.scanner import MediaItem
from ..common.motion import DURATION_SLOW, WheelInput, decelerate, make_animation
from . import geometry as geo
from .overlays import GlassPanel
from .style import STRIP_HEIGHT

THUMB_H = 44
SELECTED_GROW = 4
PLACEHOLDER = QColor(255, 255, 255, 26)


class Filmstrip(GlassPanel):
    row_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, radius=16)
        self._items: list[MediaItem] = []
        self._row = 0
        self._scroll = 0.0
        self._wheel_input = WheelInput()
        self._thumb_for: Callable[[MediaItem], QPixmap | None] | None = None
        self._animation = make_animation(self, DURATION_SLOW, decelerate(), 0.0, 1.0)
        self._animation.valueChanged.connect(self._on_scroll)
        self._from = 0.0
        self._to = 0.0
        self.setFixedHeight(STRIP_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # // data 
    def set_thumbnail_source(self, source: Callable[[MediaItem], QPixmap | None] | None) -> None:
        self._thumb_for = source

    def set_items(self, items: list[MediaItem], row: int) -> None:
        self._items = items
        self._row = row
        self.snap()

    def set_row(self, row: int, animate: bool = True) -> None:
        self._row = row
        target = self._target()
        if not animate or not self.isVisible():
            self.snap()
            return
        self._animation.stop()
        self._from, self._to = self._scroll, target
        self._animation.start()

    def preferred_width(self, limit: int) -> int:
        return min(limit, geo.strip_total(len(self._items)))

    def refresh(self) -> None:
        self.update()

    # // scrolling 
    def _target(self) -> float:
        return geo.strip_scroll(self._row, len(self._items), self.width())

    def snap(self) -> None:
        self._animation.stop()
        self._scroll = self._target()
        self.update()

    def _on_scroll(self, progress) -> None:
        self._scroll = self._from + (self._to - self._from) * float(progress)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.snap()

    # // painting 
    def _slot_rect(self, index: int) -> QRectF:
        x = geo.STRIP_PAD + index * geo.STRIP_SLOT - self._scroll
        return QRectF(x, (self.height() - THUMB_H) / 2, geo.STRIP_THUMB_W, THUMB_H)

    def _draw_thumb(self, painter: QPainter, rect: QRectF, item: MediaItem, radius: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        pixmap = self._thumb_for(item) if self._thumb_for else None
        if pixmap is None or pixmap.isNull():
            painter.fillRect(rect, PLACEHOLDER)
        else:
            ratio = pixmap.devicePixelRatio() or 1.0
            pw, ph = pixmap.width() / ratio, pixmap.height() / ratio
            scale = max(rect.width() / pw, rect.height() / ph)
            sw, sh = rect.width() / scale, rect.height() / scale
            source = QRectF((pw - sw) / 2 * ratio, (ph - sh) / 2 * ratio, sw * ratio, sh * ratio)
            painter.drawPixmap(rect, pixmap, source)
        painter.restore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 15, 15)
        painter.setClipPath(clip)
        for index in geo.strip_visible(self._scroll, self.width(), len(self._items)):
            if index == self._row:
                continue
            self._draw_thumb(painter, self._slot_rect(index), self._items[index], 6)
        if 0 <= self._row < len(self._items):  # the current one last: larger, with a white frame
            rect = self._slot_rect(self._row).adjusted(-SELECTED_GROW, -SELECTED_GROW, SELECTED_GROW, SELECTED_GROW)
            self._draw_thumb(painter, rect, self._items[self._row], 8)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)

    # // input 
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = geo.strip_slot_at(event.position().x(), self._scroll, len(self._items))
            if index is not None:
                self.row_requested.emit(index)
        event.accept()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            if self._wheel_input.is_precision(event):
                touch = self._wheel_input.steps(event)
                step = -touch
            else:
                step = -1 if delta > 0 else 1
        else:
            step = 0
        if step:
            target = max(0, min(len(self._items) - 1, self._row + step))
            if target != self._row:
                self.row_requested.emit(target)
        event.accept()
