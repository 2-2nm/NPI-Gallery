"""Crop dialog for the window background picture; the frame has the shape of the window right now."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..common.dialogs import FloatDialog, button_row, dialog_label, make_button
from ..sidebar.crop import CANVAS_SIZE, CropCanvas
from ..theme import theme
from ..theme.preferences import prefs
from .layer import backdrop, compose

PREVIEW_WIDTH = 270
PREVIEW_MAX_HEIGHT = 300
PREVIEW_RADIUS = 10.0
MOCK_SIDEBAR_SHARE = 0.2  # of the preview width: roughly the sidebar's share of the real window
MOCK_MARGIN = 6.0


class _Preview(QWidget):
    """The window as it will look: the framed picture with the sidebar card laid over it."""

    def __init__(self, size: QSize, parent=None):
        super().__init__(parent)
        self.setFixedSize(size)
        self._picture: QPixmap | None = None
        self._crop = (0.5, 0.5, 1.0)
        self._dim = 0.0

    def set_state(self, picture: QPixmap | None, crop: tuple[float, float, float], dim: float) -> None:
        self._picture, self._crop, self._dim = picture, crop, dim
        self.update()

    def paintEvent(self, event) -> None:
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, PREVIEW_RADIUS, PREVIEW_RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(bounds, QColor(palette.chrome))
        if self._picture is not None and not self._picture.isNull():
            ratio = self.devicePixelRatioF()
            size = QSize(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
            framed = compose(self._picture, self._crop, self._dim, QColor(palette.chrome), size)
            framed.setDevicePixelRatio(ratio)
            painter.drawPixmap(0, 0, framed)
        card = QRectF(MOCK_MARGIN, MOCK_MARGIN, self.width() * MOCK_SIDEBAR_SHARE, self.height() - 2 * MOCK_MARGIN)
        fill = QColor(palette.float_bg)
        fill.setAlpha(210)
        painter.setPen(QPen(QColor(palette.float_edge), 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(card, 6, 6)
        painter.end()


class BackdropCropDialog(FloatDialog):
    def __init__(
        self, parent, pixmap: QPixmap, window: QSize, crop: tuple[float, float, float], path: str | None = None
    ):
        super().__init__("Adjust window background", parent, width=CANVAS_SIZE.width() + 330)
        aspect = window.width() / max(1, window.height())
        self.canvas = CropCanvas(pixmap, aspect, crop)

        note = dialog_label(
            f"The window is {window.width()} × {window.height()} px right now, so the frame has that shape. "
            "Drag the frame or use the arrow keys to move it; drag a handle or use the mouse wheel to zoom. "
            "The picture follows the window when it is resized.",
            "detail",
            wrap=True,
        )
        self.body.addWidget(note)

        store = prefs()
        self._blurred = backdrop().blurred_pixmap(path or store.get("window_bg_path"), store.get("window_bg_blur"))
        self._dim = store.get("window_bg_dim") / 100
        width = PREVIEW_WIDTH
        height = round(width / max(aspect, 0.01))
        if height > PREVIEW_MAX_HEIGHT:
            height = PREVIEW_MAX_HEIGHT
            width = round(height * aspect)
        self.preview = _Preview(QSize(max(40, width), max(40, height)))
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
        self.preview.set_state(self._blurred, self.canvas.crop(), self._dim)

    def crop(self) -> tuple[float, float, float]:
        return self.canvas.crop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.canvas.setFocus()
