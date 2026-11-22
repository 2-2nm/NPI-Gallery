"""Zoom / pan surface of the viewer."""

import math
from collections.abc import Callable

from PySide6.QtCore import QBuffer, QByteArray, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QImage, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView

from ..common.crisp import CrispPixmapItem
from ..common.motion import DURATION_NORMAL, WheelInput, decelerate, make_animation
from ..theme.preferences import prefs
from .style import CANVAS

ZOOM_FACTOR = 1.15
WHEEL_NOTCH = 120  # angleDelta of one classic wheel click; smaller deltas (touchpads) zoom proportionally
ZOOM_GLIDE_MS = DURATION_NORMAL - 20  # how long a zoom step takes to settle
MIN_SCALE = 0.02
MAX_SCALE = 40.0
EDGE_ZONE = 0.15  # a click within this share of the width from either side steps to that photo
CLICK_SLOP = 4  # px of movement that turns a click into a pan


class ImageCanvas(QGraphicsView):
    step_requested = Signal(int)  # wheel asked for the next (+1) / previous (-1) photo
    zoom_changed = Signal(float)  # zoom in percent (100 = one image pixel per screen pixel)
    gesture_zoomed = Signal()  # the mouse wheel or a double-click just zoomed the photo (not buttons or keys)
    pressed = Signal()  # any mouse press on the photo (the window uses it to end name editing)
    caption_pressed = Signal()  # press on an empty part of the top row: the window should be dragged
    caption_double_clicked = Signal()  # double-click there: maximize / restore

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_input = WheelInput()
        self._fit = True
        self._fit_upscale = False
        # (left, top, right, bottom) px that "fit" keeps clear of the floating controls. Zooming in
        # and panning still use the whole window; only the resting position respects them.
        self._insets = (0, 0, 0, 0)
        self._fit_scale = 1.0  # the resting size; zooming out never goes below it
        self._can_step = (False, False)  # is there a previous / next photo
        self._press_pos = None
        self._moved = False
        self.caption_hit: Callable[[QPoint], bool] | None = None
        self._angle = 0
        self._pan_origin = None
        self._movie: QMovie | None = None  # an animated GIF that is playing
        self._movie_buffer: QBuffer | None = None
        self._movie_width = 0  # width of the still it replaces, so the animation keeps the same scale
        self._scene = QGraphicsScene(self)
        self._item = CrispPixmapItem()  # shows a photo that is smaller than itself sharp (text stays readable)
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(CANVAS)
        # Panning is done by hand (see mouse*Event): ScrollHandDrag forces the open / closed
        # hand cursor on the photo, and an ordinary arrow is wanted there.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Smooth zoom: every wheel step moves a goal; the view glides towards it (in log space, so
        # the speed feels the same at 5 % and at 500 %) keeping the point under the cursor still.
        self._glide_goal = 1.0
        self._glide_from = 1.0
        self._glide_anchor = QPoint()  # viewport position that must stay put
        self._glide_scene_point = QPointF()  # ...and the scene point that sits there
        self._glide = make_animation(self, ZOOM_GLIDE_MS, decelerate(), 0.0, 1.0)
        self._glide.valueChanged.connect(self._on_glide)
        self._glide.finished.connect(self._on_glide_finished)

    # // content 
    def _set_pixmap(self, pixmap: QPixmap, scale: float) -> None:
        self.stop_animation()
        self._angle = 0
        self._item.setRotation(0)
        self._item.setPixmap(pixmap)
        self._item.setScale(scale)
        self._item.setTransformOriginPoint(self._item.boundingRect().center())
        self.fit()

    def set_image(self, image: QImage) -> None:
        self._set_pixmap(QPixmap.fromImage(image), 1.0)
        self._item.set_source(image)

    def show_preview(self, pixmap: QPixmap, width: int, height: int) -> None:
        """Low-resolution stand-in, scaled to the real photo's size so the fit does not jump."""
        if pixmap.isNull() or pixmap.width() <= 0:
            return
        scale = width / pixmap.width() if width > 0 and height > 0 else 1.0
        self._set_pixmap(pixmap, scale)

    def play_animation(self, data: bytes) -> None:
        """Play an animated GIF in place of the still that `set_image` just put up. Frames are decoded
        by QMovie as it goes, so a long GIF does not have to sit in memory as hundreds of bitmaps."""
        self._drop_movie()
        if not self.has_image() or not data:
            return
        buffer = QBuffer(self)
        buffer.setData(QByteArray(data))
        buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        movie = QMovie(self)
        movie.setDevice(buffer)
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        if not movie.isValid():
            movie.deleteLater()
            buffer.deleteLater()
            return  # keep showing the first frame
        self._movie, self._movie_buffer = movie, buffer
        self._item.enabled = False  # every frame is a new pixmap: no sharp copies while it plays
        self._movie_width = self._item.pixmap().width()
        movie.frameChanged.connect(self._on_movie_frame)
        movie.start()

    def stop_animation(self) -> None:
        self._drop_movie()

    def is_animating(self) -> bool:
        return self._movie is not None

    def _drop_movie(self) -> None:
        movie, buffer = self._movie, self._movie_buffer
        self._movie = self._movie_buffer = None
        self._item.enabled = True
        if movie is not None:
            movie.stop()
            try:
                movie.frameChanged.disconnect(self._on_movie_frame)
            except (RuntimeError, TypeError):
                pass
            movie.deleteLater()
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()

    def _on_movie_frame(self, _index: int) -> None:
        if self._movie is None:
            return
        frame = self._movie.currentPixmap()
        if frame.isNull():
            return
        self._item.setPixmap(frame)
        if self._movie_width and frame.width() != self._movie_width:
            self._item.setScale(self._movie_width / frame.width())

    def clear_image(self) -> None:
        self._drop_movie()
        self._stop_glide()
        self._angle = 0
        self._item.setRotation(0)
        self._item.setScale(1.0)
        self._item.setPixmap(QPixmap())
        self._scene.setSceneRect(0, 0, 1, 1)
        self.zoom_changed.emit(100.0)

    def has_image(self) -> bool:
        return not self._item.pixmap().isNull()

    # // zoom 
    def zoom_percent(self) -> float:
        return self.transform().m11() * 100.0

    def _emit_zoom(self) -> None:
        self.zoom_changed.emit(self.zoom_percent())

    def set_insets(self, left: int, top: int, right: int, bottom: int) -> None:
        insets = (max(0, left), max(0, top), max(0, right), max(0, bottom))
        if insets == self._insets:
            return
        self._insets = insets
        if self._fit:
            self.fit(self._fit_upscale)
        else:
            self._sync_scene_rect()

    def _free_size(self) -> tuple[int, int]:
        left, top, right, bottom = self._insets
        view = self.viewport()
        return max(50, view.width() - left - right), max(50, view.height() - top - bottom)

    def _sync_scene_rect(self) -> None:
        """The scene is the photo plus the insets (in scene units, so they stay constant on screen).
        A photo that fits is then centred in the free area, and a zoomed one can be panned until
        its edges clear the controls."""
        if not self.has_image():
            return
        rect = self._item.sceneBoundingRect()
        scale = self.transform().m11()
        if scale <= 0:
            return
        left, top, right, bottom = self._insets
        centre = rect.center()
        before = self.mapFromScene(centre)
        self._scene.setSceneRect(rect.adjusted(-left / scale, -top / scale, right / scale, bottom / scale))
        after = self.mapFromScene(centre)
        if after != before:  # growing the scene moved the content; scroll it back
            horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
            horizontal.setValue(horizontal.value() + after.x() - before.x())
            vertical.setValue(vertical.value() + after.y() - before.y())

    def fit(self, upscale: bool | None = None) -> None:
        """Fit the photo into the free area (the window minus the insets). Small photos are only
        enlarged when `upscale` is true; by default that follows the "Zoom preference" setting."""
        if upscale is None:
            upscale = prefs().get("zoom_preference") == "fit"
        self._stop_glide()
        self._fit = True
        self._fit_upscale = upscale
        self.resetTransform()
        if self._item.pixmap().isNull():
            self._emit_zoom()
            return
        rect = self._item.sceneBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
            free_w, free_h = self._free_size()
            scale = min(free_w / rect.width(), free_h / rect.height())
            if not upscale:
                scale = min(scale, 1.0)
            self.scale(scale, scale)
        self._fit_scale = self.transform().m11()
        self._sync_scene_rect()
        self.centerOn(self._scene.sceneRect().center())
        self._emit_zoom()

    def actual_size(self) -> None:
        self._stop_glide()
        self._fit = False
        self.resetTransform()
        self._sync_scene_rect()
        self.centerOn(self._scene.sceneRect().center())
        self._emit_zoom()

    def is_fit(self) -> bool:
        return self._fit

    def zoom_by(self, factor: float, anchor_under_mouse: bool = False) -> None:
        if not self.has_image():
            return
        self._stop_glide()
        current = self.transform().m11()
        scale = current * factor
        if factor < 1.0 and scale <= self._fit_scale * 1.001:
            # Nobody wants the photo smaller than its resting size: zooming out lands on it.
            if current > self._fit_scale * 1.001:
                self.fit(self._fit_upscale)
            return
        if not (MIN_SCALE <= scale <= MAX_SCALE):
            return
        anchor = (
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
            if anchor_under_mouse
            else QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self.setTransformationAnchor(anchor)
        self._fit = False
        self.scale(factor, factor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._sync_scene_rect()
        self._emit_zoom()

    def zoom_smooth(self, factor: float, anchor: QPoint | None = None) -> None:
        """Like zoom_by, but glides to the new size. Repeated calls stack up: the goal moves on
        from where the last call was heading, so a fast flick of the wheel zooms far and smoothly.
        `anchor` is a viewport position that stays under the same part of the photo (default: centre)."""
        if not self.has_image() or factor <= 0 or factor == 1.0:
            return
        current = self.transform().m11()
        base = self._glide_goal if self._glide.isRunning() else current
        goal = base * factor
        floor = self._fit_scale
        if factor < 1.0 and goal <= floor * 1.001:
            if current <= floor * 1.001 and not self._glide.isRunning():
                return  # already at the resting size
            goal = floor  # zooming out lands on the resting size
        goal = min(max(goal, MIN_SCALE), MAX_SCALE)
        if abs(goal - current) < 1e-9 and not self._glide.isRunning():
            return
        self._fit = False
        self._glide_goal = goal
        self._glide_from = current
        self._glide_anchor = anchor if anchor is not None else self.viewport().rect().center()
        self._glide_scene_point = self.mapToScene(self._glide_anchor)
        self._glide.stop()
        self._glide.start()

    def _stop_glide(self) -> None:
        self._glide.stop()

    def _on_glide(self, progress: float) -> None:
        # progress runs 0 -> 1; interpolate the scale on a log axis
        target = math.exp(math.log(self._glide_from) + (math.log(self._glide_goal) - math.log(self._glide_from)) * progress)
        current = self.transform().m11()
        if current <= 0 or target <= 0:
            return
        factor = target / current
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.scale(factor, factor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._sync_scene_rect()
        moved = self.mapFromScene(self._glide_scene_point) - self._glide_anchor
        if moved.x() or moved.y():
            horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
            horizontal.setValue(horizontal.value() + moved.x())
            vertical.setValue(vertical.value() + moved.y())
        self._emit_zoom()

    def _on_glide_finished(self) -> None:
        if self._glide_goal <= self._fit_scale * 1.001:
            self.fit(self._fit_upscale)  # settle exactly on the resting size, centred

    def rotate_view(self, degrees: int = 90) -> None:
        """Rotate what is on screen (the file itself is not touched)."""
        if not self.has_image():
            return
        self._angle = (self._angle + degrees) % 360
        self._item.setRotation(self._angle)
        self.fit(self._fit_upscale)

    # // events 
    def wheelEvent(self, event) -> None:
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if not ctrl and prefs().get("mouse_wheel") == "navigate":
            delta = event.angleDelta().y()
            if delta:
                if self._wheel_input.is_precision(event):
                    step = -self._wheel_input.steps(event)  # one photo per touchpad swipe
                else:
                    step = -1 if delta > 0 else 1
                if step:
                    self.step_requested.emit(step)
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta:
            # One classic notch is one ZOOM_FACTOR; finer wheels and touchpads scale in proportion.
            self.zoom_smooth(ZOOM_FACTOR ** (delta / WHEEL_NOTCH), event.position().toPoint())
            self.gesture_zoomed.emit()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit(self._fit_upscale)

    def _in_caption(self, event) -> bool:
        if self.caption_hit is None or self.parentWidget() is None:
            return False
        # mouse events of a graphics view arrive in viewport coordinates
        return self.caption_hit(self.viewport().mapTo(self.parentWidget(), event.position().toPoint()))

    def mousePressEvent(self, event) -> None:
        self.pressed.emit()
        if event.button() == Qt.MouseButton.LeftButton and self._in_caption(event):
            self.caption_pressed.emit()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._pan_origin = event.position().toPoint()
            self._press_pos = self._pan_origin
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def set_can_step(self, previous: bool, following: bool) -> None:
        self._can_step = (previous, following)

    def _edge_step(self, x: float) -> int:
        """-1 / +1 when x is in the left / right edge zone and that photo exists, else 0."""
        width = self.viewport().width()
        if width <= 0:
            return 0
        if x <= width * EDGE_ZONE and self._can_step[0]:
            return -1
        if x >= width * (1.0 - EDGE_ZONE) and self._can_step[1]:
            return 1
        return 0

    def mouseMoveEvent(self, event) -> None:
        if self._pan_origin is None:
            hand = self._edge_step(event.position().x()) != 0 and not self._in_caption(event)
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hand else Qt.CursorShape.ArrowCursor)
        if self._pan_origin is not None:
            position = event.position().toPoint()
            if self._press_pos is not None and (position - self._press_pos).manhattanLength() > CLICK_SLOP:
                self._moved = True
            delta = position - self._pan_origin
            self._pan_origin = position
            horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
            horizontal.setValue(horizontal.value() - delta.x())
            vertical.setValue(vertical.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pan_origin is not None:
            self._pan_origin = None
            if not self._moved and self._press_pos is not None:
                step = self._edge_step(self._press_pos.x())
                if step:
                    self.step_requested.emit(step)
            self._press_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._in_caption(event):
            self.caption_double_clicked.emit()
            event.accept()
            return
        step = self._edge_step(event.position().x()) if event.button() == Qt.MouseButton.LeftButton else 0
        if step:  # fast clicking at an edge keeps stepping instead of toggling the zoom
            self.step_requested.emit(step)
            event.accept()
            return
        if self._fit:
            self.actual_size()
            self.gesture_zoomed.emit()
        else:
            self.fit()
        event.accept()