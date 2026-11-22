"""The picture while the Adjustment tool is open: the cropped result with the sliders applied, fitted to the stage.

It serves the Filter tool too: the filter goes on first, then the sliders.

The Crop tool has its own picture (`canvas.EditCanvas`, with the frame and handles). This one only shows,
so it stays small: it keeps a shrunken copy of the cropped photo and runs the sliders over that, which is
fast enough to follow a slider while it is dragged.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ...services import editing as ed
from ...services.adjust import apply_adjustments
from ...services.filters import apply_filter
from ..common.crisp import MIN_SHRINK, ShrinkCache, snapped
from ..common.imageconv import pil_to_qimage

MARGIN = 28
FIT_SIDE = 1600  # the working copy is at most this many pixels on its long side
RENDER_MS = 12  # slider moves that come faster than this are gathered into one redraw
BACKGROUND = QColor("#1c1c1c")  # same as the editor window


class AdjustView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._base = None  # upright preview of the photo (Pillow)
        self._full: tuple[int, int] = (1, 1)  # size of the real photo
        self._state = ed.EditState()
        self._small = None  # the cropped, shrunken copy; rebuilt when the crop / turns change
        self._geometry: tuple | None = None  # what `_small` was made for
        self._shown: tuple | None = None  # (geometry, filter, adjustments) of the pixmap on screen
        self._pixmap: QPixmap | None = None
        self._shrink = ShrinkCache()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RENDER_MS)
        self._timer.timeout.connect(self._render)

    def set_source(self, preview, full: tuple[int, int]) -> None:
        self._base, self._full = preview, full
        self._small, self._geometry, self._shown, self._pixmap = None, None, None, None

    def show_state(self, state: ed.EditState) -> None:
        """Show the photo as `state` makes it. Cheap to call often: it redraws only what changed."""
        self._state = state
        if (self._geometry_of(state), state.filter, state.adjust) != self._shown and not self._timer.isActive():
            self._timer.start()

    @staticmethod
    def _geometry_of(state: ed.EditState) -> tuple:
        return state.ops, state.angle, state.crop

    def _render(self) -> None:
        if self._base is None:
            return
        state = self._state
        geometry = self._geometry_of(state)
        if self._small is None or geometry != self._geometry:
            self._small = ed.render_preview(self._base, state, self._full, FIT_SIDE)
            self._geometry = geometry
        shown = apply_adjustments(apply_filter(self._small, state.filter), state.adjust)
        self._pixmap = QPixmap.fromImage(pil_to_qimage(shown))
        self._shown = (geometry, state.filter, state.adjust)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        if self._pixmap is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            width, height = self._pixmap.width(), self._pixmap.height()
            scale = max(0.01, min((self.width() - 2 * MARGIN) / width, (self.height() - 2 * MARGIN) / height))
            target = QRectF(0, 0, width * scale, height * scale)
            target.moveCenter(QRectF(self.rect()).center())
            ratio = self.devicePixelRatioF()
            wanted = (round(target.width() * ratio), round(target.height() * ratio))
            if wanted[0] < width * MIN_SHRINK:  # shrunk properly first, bilinear alone would lose fine detail
                copy = self._shrink.get(self._pixmap, wanted[0], wanted[1], ratio)
                painter.drawPixmap(snapped(target.topLeft(), ratio), copy)
            else:
                painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        painter.end()