"""Sharp shrinking for photos that are shown smaller than they are.

Qt draws a pixmap at a smaller size with plain bilinear sampling: every screen pixel looks at only the 2x2
source pixels around it and the rest are skipped. That is fine for a photo, but fine detail such as the text
of a screenshot loses strokes and turns to mush (at fullscreen the scale is close to 1:1, which is why the
text only seemed to appear there). Here the photo is first shrunk to the exact size it will have on screen
with Qt's area-weighted `SmoothTransformation`, which uses every source pixel, then blitted 1:1, aligned to the
device pixels like Windows Photos does.

    shrunk(image, w, h, ratio)      one shrunk copy, `ratio` = the screen's device pixel ratio
    ShrinkCache                     the last shrunk copy of a pixmap (used by the editor canvases)
    CrispPixmapItem                 QGraphicsPixmapItem that does all of it by itself (used by the viewers)
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem

SETTLE_MS = 70  # the viewer waits this long after the last zoom / resize step before it builds a sharp copy
MIN_SHRINK = 0.999  # at or above this scale the photo is not shrunk and Qt's own drawing is fine


def shrunk(image: QImage, width: int, height: int, ratio: float = 1.0) -> QPixmap:
    """`image` at exactly `width` x `height` device pixels, as a pixmap that draws at that size on screen."""
    scaled = image.scaled(
        max(1, width), max(1, height), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    pixmap = QPixmap.fromImage(scaled)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def snapped(point: QPointF, ratio: float) -> QPointF:
    """`point` moved to the nearest device pixel, so a 1:1 blit is not smeared over two pixels."""
    return QPointF(round(point.x() * ratio) / ratio, round(point.y() * ratio) / ratio)


class ShrinkCache:
    """The most recent shrunk copy of one pixmap; rebuilt when the pixmap or the wanted size changes."""

    def __init__(self) -> None:
        self._copy: QPixmap | None = None
        self._key: tuple | None = None

    def clear(self) -> None:
        self._copy = None
        self._key = None

    def get(self, source: QPixmap, width: int, height: int, ratio: float) -> QPixmap:
        key = (source.cacheKey(), width, height, ratio)
        if self._copy is None or self._key != key:
            self._copy = shrunk(source.toImage(), width, height, ratio)
            self._key = key
        return self._copy


class CrispPixmapItem(QGraphicsPixmapItem):
    """A pixmap item that, while it is shown smaller than it is, paints a pre-shrunk copy instead.

    The copy is made once the zoom or the window size has settled; until then (and for rotated or sheared
    views, and for animations) Qt draws the item as usual, so nothing ever waits for it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.enabled = True  # off while an animation plays: every frame would need a new copy
        self._source: QImage | None = None
        self._copy: QPixmap | None = None
        self._want: tuple[int, int, float] | None = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self._build)

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802 (Qt name)
        self._source = None
        self._copy = None
        self._want = None
        self._timer.stop()
        super().setPixmap(pixmap)

    def set_source(self, image: QImage) -> None:
        """The full-quality image behind the pixmap just set (so the pixmap need not be converted back)."""
        self._source = image
        self._copy = None

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pixmap = self.pixmap()
        world = painter.worldTransform()
        scale = world.m11()
        straight = abs(world.m12()) < 1e-9 and abs(world.m21()) < 1e-9 and abs(world.m22() - scale) < 1e-6
        if not self.enabled or pixmap.isNull() or not straight or scale >= MIN_SHRINK:
            super().paint(painter, option, widget)
            return
        ratio = painter.device().devicePixelRatioF()
        width = max(1, round(pixmap.width() * scale * ratio))
        height = max(1, round(pixmap.height() * scale * ratio))
        copy = self._copy
        if copy is not None and copy.width() == width and copy.height() == height:
            origin = snapped(world.map(self.offset()), ratio)
            painter.save()
            painter.setWorldTransform(QTransform())  # the clip stays; the copy is already at screen size
            painter.drawPixmap(origin, copy)
            painter.restore()
            return
        super().paint(painter, option, widget)  # the quick way for now ...
        if self._want != (width, height, ratio) or not self._timer.isActive():
            self._want = (width, height, ratio)
            self._timer.start()  # ... and a sharp copy as soon as the size stops changing

    def _build(self) -> None:
        want = self._want
        pixmap = self.pixmap()
        if want is None or pixmap.isNull() or not self.enabled:
            return
        image = self._source if self._source is not None and not self._source.isNull() else pixmap.toImage()
        self._copy = shrunk(image, want[0], want[1], want[2])
        self.update()
