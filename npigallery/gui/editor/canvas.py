"""The picture with the crop frame over it, in the style of the Windows Photos crop tool.

The picture is drawn turned by the straighten angle; the frame is always axis-aligned and is kept
inside the turned picture (see `services.editing`).

Like Windows Photos, the view follows the frame: the frame is zoomed to fill the stage and sits in its
centre, and the rest of the picture shows dimmed around it. Drag inside the frame (or use the arrow
keys) to slide the picture under the frame, drag a handle or use the wheel to resize the frame. While a
handle is dragged the view stands still; when it is let go the view zooms to the new frame.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget

from ...services import editing as ed
from ...services.adjust import Adjustments, apply_adjustments
from ...services.filters import FilterChoice, apply_filter
from ..common.crisp import MIN_SHRINK, ShrinkCache, snapped
from ..common.imageconv import pil_to_qimage

MARGIN = 28
HANDLE_REACH = 14  # how close (px) the pointer must be to a handle to grab it
ARROW_STEP = 8  # px of the on-screen picture per arrow key press
COMMIT_MS = 350  # wheel / arrow key steps are gathered into one undo step
MAX_ZOOM = 8.0  # biggest view scale: screen px per photo px (8.0 = 800 %)
ZOOM_MS = 200  # how long the view takes to zoom to a new frame
FRAME_COLOR = QColor("#ffffff")
BACKGROUND = QColor("#1c1c1c")  # same as the editor window, so the picture area has no darker box

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

Snapshot = tuple[ed.EditState, "float | None"]  # the state and the locked shape (None = free)
View = tuple[float, float, float]  # scale (screen px per photo px) and the centre (x, y) in frame coordinates


class EditCanvas(QWidget):
    changed = Signal()  # the state changed (also while dragging)
    committed = Signal()  # a gesture is finished: one undo step

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base = None  # upright preview (Pillow)
        self._full: tuple[int, int] = (1, 1)  # size of the real photo
        self._state = ed.EditState()
        self._aspect: float | None = None
        self._pixmap: QPixmap | None = None
        self._shrink = ShrinkCache()  # the picture shrunk to its size on screen (sharp text)
        self._stale = False  # the pixmap no longer matches the state (redrawn when the canvas is shown)
        self._drag: tuple | None = None
        self._moved = False
        self._view: View = (1.0, 0.0, 0.0)  # what is on screen now; follows the frame (see `_refit`)
        self._zoom_from: View = self._view
        self._zoom_to: View = self._view
        self._zoom = QVariantAnimation(self)
        self._zoom.setStartValue(0.0)
        self._zoom.setEndValue(1.0)
        self._zoom.setDuration(ZOOM_MS)
        self._zoom.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._zoom.valueChanged.connect(self._on_zoom)
        self._zoom.finished.connect(self.update)  # the last frame of the zoom is drawn sharp
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COMMIT_MS)
        self._timer.timeout.connect(self._settle)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # // state

    def set_source(self, preview, full: tuple[int, int]) -> None:
        self._base, self._full = preview, full
        self._state = ed.EditState()
        self._aspect = None
        self._rebuild()
        self._refit(animate=False)
        self.update()
        self.changed.emit()

    def has_source(self) -> bool:
        return self._base is not None

    def state(self) -> ed.EditState:
        return self._state

    def aspect(self) -> float | None:
        return self._aspect

    def snapshot(self) -> Snapshot:
        return self._state, self._aspect

    def restore(self, snapshot: Snapshot) -> None:
        state, aspect = snapshot
        turned = state.ops != self._state.ops
        redraw = turned or state.adjust != self._state.adjust or state.filter != self._state.filter
        self._state, self._aspect = state, aspect
        if redraw:
            self._rebuild()
        self._refit(animate=not turned)  # a turn / flip changes the frame's coordinates: no sweep across them
        self.update()
        self.changed.emit()

    def is_unchanged(self) -> bool:
        return self._state.is_identity(self._full)

    def _rebuild(self) -> None:
        """The picture must be redrawn from the state: now when it is on screen, else when it is shown."""
        self._stale = True
        if self.isVisible() or self._base is None:
            self.refresh()

    def refresh(self) -> None:
        if not self._stale:
            return
        self._stale = False
        if self._base is None:
            self._pixmap = None
            return
        # the vignette is left out: it belongs to the cropped result, not to the whole picture shown here
        image = apply_filter(ed.apply_ops(self._base, self._state.ops), self._state.filter)
        image = apply_adjustments(image, self._state.adjust, vignette=False)
        self._pixmap = QPixmap.fromImage(pil_to_qimage(image))
        self.update()

    def _work(self) -> tuple[float, float]:
        return ed.working_size(self._full, self._state.ops)

    def _rect(self) -> ed.Rect:
        if self._state.crop is not None:
            return self._state.crop
        w, h = self._work()
        return 0.0, 0.0, w, h

    def _set_rect(self, rect) -> None:
        self._state = replace(self._state, crop=tuple(float(v) for v in rect))
        self.update()
        self.changed.emit()

    def _commit_later(self) -> None:
        self._timer.start()

    def _settle(self) -> None:
        """The wheel / arrow keys paused: make sure the view fits the frame, and close the undo step."""
        self._refit(animate=True)
        self.committed.emit()

    # // view (follows the frame)

    def _fit_view(self) -> View:
        """The view that shows the frame as big as the stage allows (up to MAX_ZOOM), in the stage's centre."""
        x, y, w, h = self._rect()
        room_w = max(1.0, self.width() - 2 * MARGIN)
        room_h = max(1.0, self.height() - 2 * MARGIN)
        scale = min(room_w / max(w, 1e-6), room_h / max(h, 1e-6), MAX_ZOOM)
        return max(0.01, scale), x + w / 2, y + h / 2

    def _refit(self, animate: bool) -> None:
        """Bring the view to the frame: zoom there smoothly, or jump when `animate` is False."""
        target = self._fit_view()
        self._zoom.stop()
        if not animate or self._pixmap is None or not self.isVisible() or self._view == target:
            self._view = target
            self.update()
            return
        self._zoom_from, self._zoom_to = self._view, target
        self._zoom.start()

    def _on_zoom(self, value) -> None:
        t = float(value)
        (s0, x0, y0), (s1, x1, y1) = self._zoom_from, self._zoom_to
        self._view = (s0 * (s1 / s0) ** t, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)  # scale in equal ratios
        self.update()

    def _follow_frame(self) -> None:
        """Keep the scale, but put the view's centre on the frame (the frame stays still on screen)."""
        self._zoom.stop()
        x, y, w, h = self._rect()
        self._view = (self._view[0], x + w / 2, y + h / 2)

    # // commands from the panel

    def set_adjust(self, adjust: Adjustments) -> None:
        """The Adjustment tool changed a slider; this picture catches up when it is shown again."""
        if adjust == self._state.adjust:
            return
        self._state = replace(self._state, adjust=adjust)
        self._rebuild()
        self.changed.emit()

    def set_filter(self, choice: FilterChoice) -> None:
        """The Filter tool changed the look or its intensity; this picture catches up when it is shown again."""
        if choice == self._state.filter:
            return
        self._state = replace(self._state, filter=choice)
        self._rebuild()
        self.changed.emit()

    def set_aspect(self, aspect: float | None) -> None:
        """Lock the frame to a shape (w / h), or None to free it; a locked frame becomes the biggest of that shape."""
        self._aspect = aspect
        if aspect is not None:
            self._set_rect(ed.largest_rect(self._work(), self._state.angle, aspect))
            self._refit(animate=True)
        self.committed.emit()

    def original_aspect(self) -> float:
        w, h = self._work()
        return w / max(h, 1)

    def set_angle(self, degrees: float) -> None:
        degrees = max(-ed.MAX_ANGLE, min(ed.MAX_ANGLE, degrees))
        self._state = replace(self._state, angle=degrees)
        self._set_rect(ed.fit_rect(self._rect(), self._work(), degrees))
        self._refit(animate=False)  # the frame shrinks as the picture is straightened: the view keeps up

    def turn(self, clockwise: bool) -> None:
        self._op("cw" if clockwise else "ccw")
        if self._aspect:
            self._aspect = 1 / self._aspect

    def flip(self, horizontal: bool) -> None:
        self._op("fh" if horizontal else "fv")

    def _op(self, op: str) -> None:
        if self._base is None:
            return
        self._state = ed.op_state(self._state, self._work(), op)
        self._rebuild()
        self._refit(animate=False)
        self.update()
        self.changed.emit()
        self.committed.emit()

    # // geometry

    def _scale(self) -> float:
        return self._view[0]

    def _centre(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _pivot(self) -> QPointF:
        """Where the middle of the picture (the point it is turned about) is on screen."""
        W, H = self._work()
        s, vx, vy = self._view
        c = self._centre()
        return QPointF(c.x() + (W / 2 - vx) * s, c.y() + (H / 2 - vy) * s)

    def _frame(self) -> QRectF:
        x, y, w, h = self._rect()
        s, vx, vy = self._view
        c = self._centre()
        return QRectF(c.x() + (x - vx) * s, c.y() + (y - vy) * s, w * s, h * s)

    def _to_work(self, point: QPointF) -> QPointF:
        s, vx, vy = self._view
        c = self._centre()
        return QPointF((point.x() - c.x()) / s + vx, (point.y() - c.y()) / s + vy)

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
        painter.fillRect(self.rect(), BACKGROUND)
        if self._pixmap is None:
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self._work()
        s, pivot = self._scale(), self._pivot()
        ratio = self.devicePixelRatioF()
        wanted = (round(W * s * ratio), round(H * s * ratio))
        crisp = (
            abs(self._state.angle) < 1e-6  # a straightened picture is resampled by the rotation anyway
            and self._zoom.state() != QAbstractAnimation.State.Running
            and wanted[0] < self._pixmap.width() * MIN_SHRINK
        )
        if crisp:  # smaller than the picture itself: draw a properly shrunk copy 1:1 (bilinear would lose text)
            copy = self._shrink.get(self._pixmap, wanted[0], wanted[1], ratio)
            painter.drawPixmap(snapped(QPointF(pivot.x() - W * s / 2, pivot.y() - H * s / 2), ratio), copy)
        else:
            painter.save()
            painter.translate(pivot)
            painter.rotate(self._state.angle)  # clockwise on screen
            painter.drawPixmap(QRectF(-W * s / 2, -H * s / 2, W * s, H * s), self._pixmap, QRectF(self._pixmap.rect()))
            painter.restore()

        frame = self._frame()
        # Dim only the picture outside the frame (turned like the picture), never the background around it.
        picture = QPainterPath()
        picture.addRect(QRectF(-W * s / 2, -H * s / 2, W * s, H * s))
        picture = QTransform().translate(pivot.x(), pivot.y()).rotate(self._state.angle).map(picture)
        inside = QPainterPath()
        inside.addRect(frame)
        painter.fillPath(picture.subtracted(inside), QColor(0, 0, 0, 150))

        # 8:52 PM, June 15 2000

        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))  # rule-of-thirds guides
        for i in (1, 2):
            x = frame.left() + frame.width() * i / 3
            y = frame.top() + frame.height() * i / 3
            painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
            painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))

        painter.setPen(QPen(FRAME_COLOR, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(frame)

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
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap is None:
            return
        point = event.position()
        handle = self._handle_at(point)
        x, y, w, h = self._rect()
        if handle is not None:
            hx, hy = handle
            ax = x + w if hx < 0 else x if hx > 0 else x + w / 2
            ay = y + h if hy < 0 else y if hy > 0 else y + h / 2
            self._drag = ("size", handle, QPointF(ax, ay), (x, y, w, h))
        elif self._frame().contains(point):
            self._drag = ("pan", point, (x, y, w, h))  # the pointer's place on screen: the frame stays still
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._drag is not None:
            self._zoom.stop()  # a zoom in progress stops where it is; the view stands still while dragging
        self._moved = False
        self.setFocus()

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._drag is None:
            handle = self._handle_at(point) if self._pixmap is not None else None
            if handle is not None:
                self.setCursor(_CURSORS[handle])
            elif self._pixmap is not None and self._frame().contains(point):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
            return
        size, angle, current = self._work(), self._state.angle, self._rect()
        if self._drag[0] == "pan":
            _kind, start, (x, y, w, h) = self._drag
            scale = self._view[0]
            # the picture follows the pointer, so the frame moves the other way over the picture
            dx, dy = (start.x() - point.x()) / scale, (start.y() - point.y()) / scale
            # x and y one after the other, so the picture slides along the frame's edge
            step = ed.approach(current, (x + dx, current[1], w, h), size, angle)
            rect = ed.approach(step, (step[0], y + dy, w, h), size, angle)
            self._moved = True
            self._set_rect(rect)
            self._follow_frame()
            return
        _kind, (hx, hy), anchor, start_rect = self._drag
        rect = ed.approach(current, self._resized(hx, hy, anchor, self._to_work(point), start_rect), size, angle)
        self._moved = True
        self._set_rect(rect)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.mouseMoveEvent(event)
            if self._moved:
                self.committed.emit()
            self._refit(animate=True)  # zoom to the frame as it is now

    def _resized(self, hx: int, hy: int, anchor: QPointF, pointer: QPointF, start: ed.Rect) -> ed.Rect:
        """The frame for a handle dragged to `pointer` (picture pixels), the opposite side staying put."""
        minimum = ed.MIN_SIDE
        x, y, w, h = start
        if self._aspect is None:
            left, top, right, bottom = x, y, x + w, y + h
            if hx < 0:
                left = min(pointer.x(), right - minimum)
            elif hx > 0:
                right = max(pointer.x(), left + minimum)
            if hy < 0:
                top = min(pointer.y(), bottom - minimum)
            elif hy > 0:
                bottom = max(pointer.y(), top + minimum)
            return left, top, right - left, bottom - top
        wanted = []
        if hx:
            wanted.append(abs(pointer.x() - anchor.x()))
        if hy:
            wanted.append(abs(pointer.y() - anchor.y()) * self._aspect)
        width = max(max(wanted), minimum * max(1.0, self._aspect))
        height = width / self._aspect
        left = anchor.x() if hx > 0 else anchor.x() - width if hx < 0 else anchor.x() - width / 2
        top = anchor.y() if hy > 0 else anchor.y() - height if hy < 0 else anchor.y() - height / 2
        return left, top, width, height

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps and self._pixmap is not None:
            factor = 1.1 ** (-steps)  # wheel up = zoom in = a smaller frame
            x, y, w, h = self._rect()
            nw = max(w * factor, ed.MIN_SIDE)
            nh = max(h * nw / w, ed.MIN_SIDE)
            nw = nh * w / h
            proposed = (x + (w - nw) / 2, y + (h - nh) / 2, nw, nh)
            self._set_rect(ed.approach((x, y, w, h), proposed, self._work(), self._state.angle))
            self._refit(animate=True)
            self._commit_later()
        event.accept()

    def keyPressEvent(self, event) -> None:
        moves = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        move = moves.get(event.key())
        if move is None or self._pixmap is None:
            super().keyPressEvent(event)
            return
        step = ARROW_STEP * (4 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1) / self._scale()
        x, y, w, h = self._rect()
        size, angle = self._work(), self._state.angle
        # the picture moves the way the key points, so the frame moves the other way over it
        rect = ed.approach((x, y, w, h), (x - move[0] * step, y - move[1] * step, w, h), size, angle)
        self._set_rect(rect)
        self._follow_frame()
        self._commit_later()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._drag is None:
            self._refit(animate=False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._refit(animate=False)