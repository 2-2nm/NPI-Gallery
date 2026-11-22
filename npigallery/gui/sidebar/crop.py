"""Crop dialog for the sidebar picture, in the style of the Windows Photos crop tool.

The frame always has the shape of the sidebar as it is right now (its width and height change when
it is dragged), so what is inside the frame is exactly what the sidebar will show. Drag inside the
frame (or use the arrow keys) to move it over the picture, drag a handle or use the wheel to zoom.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..common.dialogs import FloatDialog, button_row, dialog_label, make_button
from ..theme.preferences import prefs
from .panel import SidebarCard
from .background import DEFAULT_CROP, MAX_ZOOM, background_pixmap, crop_region

CANVAS_SIZE = QSize(520, 400)
PREVIEW_HEIGHT = 300
HANDLE_REACH = 12  # how close (px) the pointer must be to a handle to grab it
ARROW_STEP = 8  # px of the on-screen picture per arrow key press
FRAME_COLOR = QColor("#ffffff")

# (horizontal side, vertical side): -1 = left/top, +1 = right/bottom, 0 = middle
_HANDLES = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]
_CURSORS = {
    (-1, -1): Qt.CursorShape.SizeFDiagCursor,
    (1, 1): Qt.CursorShape.SizeFDiagCursor,
    (1, -1): Qt.CursorShape.SizeBDiagCursor,
    (-1, 1): Qt.CursorShape.SizeBDiagCursor,
    (0, -1): Qt.CursorShape.SizeVerCursor,
    (0, 1): Qt.CursorShape.SizeVerCursor,
    (-1, 0): Qt.CursorShape.SizeHorCursor,
    (1, 0): Qt.CursorShape.SizeHorCursor,
}


class CropCanvas(QWidget):
    """The picture with a fixed-shape frame over it."""

    changed = Signal()

    def __init__(self, pixmap: QPixmap, aspect: float, crop: tuple[float, float, float], parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._aspect = max(aspect, 0.01)
        self._cx, self._cy, self._zoom = crop
        self._drag: tuple | None = None
        self.setFixedSize(CANVAS_SIZE)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._normalize()

    # // state
    def crop(self) -> tuple[float, float, float]:
        return self._cx, self._cy, self._zoom

    def reset(self) -> None:
        self._cx, self._cy, self._zoom = DEFAULT_CROP
        self._normalize()
        self.changed.emit()
        self.update()

    def _normalize(self) -> None:
        """Clamp zoom, and the centre so the frame stays inside the picture."""
        pw, ph = self._pixmap.width(), self._pixmap.height()
        self._zoom = max(1.0, min(MAX_ZOOM, self._zoom))
        x, y, w, h = crop_region(pw, ph, self._aspect, self._cx, self._cy, self._zoom)
        self._cx, self._cy = (x + w / 2) / pw, (y + h / 2) / ph

    def _base_width(self) -> float:
        return min(self._pixmap.width(), self._pixmap.height() * self._aspect)

    # // geometry
    def _image_rect(self) -> QRectF:
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        rect = QRectF(0, 0, pw * scale, ph * scale)
        rect.moveCenter(QRectF(self.rect()).center())
        return rect

    def _scale(self) -> float:
        return self._image_rect().width() / self._pixmap.width()

    def _frame_image(self) -> QRectF:
        """The frame in picture pixels."""
        return QRectF(*crop_region(self._pixmap.width(), self._pixmap.height(), self._aspect, *self.crop()))

    def _frame(self) -> QRectF:
        """The frame in widget pixels."""
        image, scale = self._image_rect(), self._scale()
        f = self._frame_image()
        return QRectF(image.left() + f.left() * scale, image.top() + f.top() * scale, f.width() * scale, f.height() * scale)

    def _to_image(self, point: QPointF) -> QPointF:
        image, scale = self._image_rect(), self._scale()
        return QPointF((point.x() - image.left()) / scale, (point.y() - image.top()) / scale)

    @staticmethod
    def _handle_point(frame: QRectF, handle: tuple[int, int]) -> QPointF:
        hx, hy = handle
        x = frame.left() if hx < 0 else frame.right() if hx > 0 else frame.center().x()
        y = frame.top() if hy < 0 else frame.bottom() if hy > 0 else frame.center().y()
        return QPointF(x, y)

    def _handle_at(self, point: QPointF) -> tuple[int, int] | None:
        frame = self._frame()
        best, best_distance = None, HANDLE_REACH
        for handle in _HANDLES:
            target = self._handle_point(frame, handle)
            distance = max(abs(target.x() - point.x()), abs(target.y() - point.y()))
            if distance <= best_distance:
                best, best_distance = handle, distance
        return best

    # // painting
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        image = self._image_rect()
        painter.drawPixmap(image, self._pixmap, QRectF(self._pixmap.rect()))
        frame = self._frame()

        outside = QPainterPath()
        outside.addRect(image)
        inside = QPainterPath()
        inside.addRect(frame)
        painter.fillPath(outside.subtracted(inside), QColor(0, 0, 0, 150))

        # rule-of-thirds guides
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for i in (1, 2):
            x = frame.left() + frame.width() * i / 3
            y = frame.top() + frame.height() * i / 3
            painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
            painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))

        painter.setPen(QPen(FRAME_COLOR, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(frame)

        # Corner brackets and edge bars, like the Photos crop tool.
        painter.setPen(QPen(FRAME_COLOR, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        corner, bar = 16, 20
        for hx, hy in _HANDLES:
            p = self._handle_point(frame, (hx, hy))
            if hx and hy:
                painter.drawLine(p, QPointF(p.x() - hx * corner, p.y()))
                painter.drawLine(p, QPointF(p.x(), p.y() - hy * corner))
            elif hx:
                painter.drawLine(QPointF(p.x(), p.y() - bar / 2), QPointF(p.x(), p.y() + bar / 2))
            else:
                painter.drawLine(QPointF(p.x() - bar / 2, p.y()), QPointF(p.x() + bar / 2, p.y()))
        painter.end()

    # // mouse / keyboard
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        handle = self._handle_at(point)
        if handle is not None:
            f = self._frame_image()
            hx, hy = handle
            ax = f.right() if hx < 0 else f.left() if hx > 0 else f.center().x()
            ay = f.bottom() if hy < 0 else f.top() if hy > 0 else f.center().y()
            self._drag = ("size", handle, QPointF(ax, ay))
        elif self._frame().contains(point):
            self._drag = ("pan", point, (self._cx, self._cy))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.setFocus()

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._drag is None:
            handle = self._handle_at(point)
            if handle is not None:
                self.setCursor(_CURSORS[handle])
            elif self._frame().contains(point):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if self._drag[0] == "pan":
            _kind, start, (cx, cy) = self._drag
            scale = self._scale()
            # The picture stays put on screen and the frame moves, so the frame follows the pointer.
            self._cx = cx + (point.x() - start.x()) / scale / pw
            self._cy = cy + (point.y() - start.y()) / scale / ph
        else:
            _kind, (hx, hy), anchor = self._drag
            self._resize(hx, hy, anchor, self._to_image(point))
        self._normalize()
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.mouseMoveEvent(event)

    def _resize(self, hx: int, hy: int, anchor: QPointF, pointer: QPointF) -> None:
        """Resize from the handle (hx, hy) keeping the opposite side/corner (`anchor`) fixed and the shape."""
        pw, ph = self._pixmap.width(), self._pixmap.height()
        base = self._base_width()
        wanted = []
        if hx:
            wanted.append(abs(pointer.x() - anchor.x()))
        if hy:
            wanted.append(abs(pointer.y() - anchor.y()) * self._aspect)
        width = max(wanted)
        room_x = pw - anchor.x() if hx > 0 else anchor.x() if hx < 0 else 2 * min(anchor.x(), pw - anchor.x())
        room_y = ph - anchor.y() if hy > 0 else anchor.y() if hy < 0 else 2 * min(anchor.y(), ph - anchor.y())
        width = min(width, room_x, room_y * self._aspect, base)
        width = max(width, base / MAX_ZOOM)
        height = width / self._aspect
        left = anchor.x() if hx > 0 else anchor.x() - width if hx < 0 else anchor.x() - width / 2
        top = anchor.y() if hy > 0 else anchor.y() - height if hy < 0 else anchor.y() - height / 2
        self._cx, self._cy = (left + width / 2) / pw, (top + height / 2) / ph
        self._zoom = base / width

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self._zoom *= 1.12**steps
            self._normalize()
            self.changed.emit()
            self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:
        step = ARROW_STEP * (4 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
        moves = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        move = moves.get(event.key())
        if move is None:
            super().keyPressEvent(event)
            return
        scale = self._scale()
        # Arrow keys move the frame in the direction of the arrow, like dragging it.
        self._cx += move[0] * step / scale / self._pixmap.width()
        self._cy += move[1] * step / scale / self._pixmap.height()
        self._normalize()
        self.changed.emit()
        self.update()


class SidebarCropDialog(FloatDialog):
    def __init__(
        self, parent, pixmap: QPixmap, panel: QSize, crop: tuple[float, float, float], path: str | None = None
    ):
        super().__init__("Adjust sidebar picture", parent, width=CANVAS_SIZE.width() + 330)
        aspect = panel.width() / max(1, panel.height())
        self.canvas = CropCanvas(pixmap, aspect, crop)

        note = dialog_label(
            f"The sidebar is {panel.width()} × {panel.height()} px right now, so the frame has that shape. "
            "Drag the frame or use the arrow keys to move it; drag a handle or use the mouse wheel to zoom.",
            "detail",
            wrap=True,
        )
        self.body.addWidget(note)

        # What the sidebar will look like, with the current dimming and blur.
        store = prefs()
        self._blurred = background_pixmap(path or store.get("sidebar_bg_path"), store.get("sidebar_bg_blur"))
        self._dim = store.get("sidebar_bg_dim") / 100
        self.preview = SidebarCard()
        self.preview.setObjectName("SidebarCard")
        height = PREVIEW_HEIGHT
        self.preview.setFixedSize(max(40, min(260, round(height * aspect))), height)
        preview_column = QVBoxLayout()
        preview_column.setSpacing(8)
        preview_column.addWidget(dialog_label("Preview", "label"))
        preview_column.addWidget(self.preview)
        preview_column.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(20)
        row.addWidget(self.canvas)
        row.addLayout(preview_column)
        row.addStretch(1)
        self.body.addLayout(row)

        reset = make_button("Reset", "secondary")
        reset.clicked.connect(self.canvas.reset)
        cancel = make_button("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        done = make_button("Done", "primary")
        done.clicked.connect(self.accept)
        self.body.addSpacing(4)
        self.body.addLayout(button_row(reset, cancel, done))

        self.canvas.changed.connect(self._update_preview)
        self._update_preview()
        self.finish()

    def _update_preview(self) -> None:
        self.preview.set_picture(self._blurred, self._dim, self.canvas.crop())

    def crop(self) -> tuple[float, float, float]:
        return self.canvas.crop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.canvas.setFocus()