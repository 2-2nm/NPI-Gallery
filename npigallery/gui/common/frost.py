"""A frosted (blurred) copy of the window content behind a popup, for the acrylic look of menus.

The popup is a separate top-level window, so the picture behind it is taken from the application
window it was opened over (that part is simply painted again, blurred) rather than from the screen.
"""

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

BLUR_RADIUS = 14  # logical pixels; "a little" blur, the content stays recognisable
EDGE_MARGIN = 20  # extra pixels grabbed around the popup so the blur does not darken its edges


def _blur(image: QImage, radius: float) -> QImage:
    """Cheap, smooth blur: average the picture down a few steps, then scale it back up."""
    width, height = image.width(), image.height()
    step = max(2, round(radius / 2))
    small = image.scaled(
        max(1, width // step), max(1, height // step), Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    smaller = small.scaled(
        max(1, small.width() // 2), max(1, small.height() // 2), Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    softer = smaller.scaled(small.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return softer.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def frosted_backdrop(window: QWidget, global_top_left: QPoint, size: QSize, radius: float = BLUR_RADIUS) -> QPixmap | None:
    """The blurred part of `window` that lies under a popup of `size` at `global_top_left`.

    The result has the popup's size (and the screen's pixel ratio) and can be painted at (0, 0) in it.
    None when nothing of the window is behind the popup.
    """
    if window is None or not window.isVisible() or size.isEmpty():
        return None
    origin = window.mapFromGlobal(global_top_left)
    wanted = QRect(origin, size).adjusted(-EDGE_MARGIN, -EDGE_MARGIN, EDGE_MARGIN, EDGE_MARGIN)
    source = wanted.intersected(window.rect())
    if source.isEmpty():
        return None
    grabbed = window.grab(source)
    if grabbed.isNull():
        return None
    ratio = grabbed.devicePixelRatio()
    image = grabbed.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    blurred = _blur(image, radius * ratio)
    blurred.setDevicePixelRatio(ratio)

    result = QPixmap(round(size.width() * ratio), round(size.height() * ratio))
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawImage(QPointF(source.left() - origin.x(), source.top() - origin.y()), blurred)
    painter.end()
    return result