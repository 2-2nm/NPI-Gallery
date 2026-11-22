import io
import os

from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...formatting import format_timestamp, human_size
from ..common.crisp import CrispPixmapItem
from ...npi import (
    ImageInfo,
    inspect_image,
    load_oriented_rgba,
    read_exif_tags,
    read_gps,
    read_image_bytes,
    read_meta,
)
from ...services.scanner import MediaItem
from ..common.branding import style_title_bar
from ..common.imageconv import pil_to_qimage
from ..theme.preferences import prefs

ZOOM_FACTOR = 1.15
MIN_SCALE = 0.02
MAX_SCALE = 40.0
PANEL_WIDTH = 340
SLIDESHOW_INTERVAL_MS = 4000
CANVAS_BACKGROUND = QColor(14, 14, 14)


class ImageCanvas(QGraphicsView):
    step_requested = Signal(int)  # mouse wheel asked for the next (+1) or previous (-1) photo

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fit_upscale = False
        self._scene = QGraphicsScene(self)
        self._item = CrispPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self._fit = True
        self.setScene(self._scene)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(CANVAS_BACKGROUND)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_image(self, image: QImage) -> None:
        self._item.setPixmap(QPixmap.fromImage(image))
        self._item.set_source(image)
        self._scene.setSceneRect(self._item.boundingRect())
        self.fit()

    def clear_image(self) -> None:
        self._item.setPixmap(QPixmap())
        self._scene.setSceneRect(0, 0, 1, 1)

    def fit(self, upscale: bool | None = None) -> None:
        """Fit the photo to the window. Small photos are only enlarged when `upscale` is true;
        by default that follows the "Zoom preference" setting."""
        if upscale is None:
            upscale = prefs().get("zoom_preference") == "fit"
        self._fit = True
        self._fit_upscale = upscale
        self.resetTransform()
        if self._item.pixmap().isNull():
            return
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        if not upscale and self.transform().m11() > 1.0:
            self.resetTransform()

    def actual_size(self) -> None:
        self._fit = False
        self.resetTransform()

    def wheelEvent(self, event) -> None:
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if not ctrl and prefs().get("mouse_wheel") == "navigate":
            delta = event.angleDelta().y()
            if delta:
                self.step_requested.emit(-1 if delta > 0 else 1)
            event.accept()
            return
        factor = ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / ZOOM_FACTOR
        scale = self.transform().m11() * factor
        if MIN_SCALE <= scale <= MAX_SCALE:
            self._fit = False
            self.scale(factor, factor)
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit(self._fit_upscale)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._fit:
            self.actual_size()
        else:
            self.fit()
        event.accept()


class InfoPanel(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setFixedWidth(PANEL_WIDTH)

    def populate(self, sections: list[tuple[str, list[tuple[str, str]]]]) -> None:
        self.clear()
        for title, rows in sections:
            if not rows:
                continue
            header = QTreeWidgetItem([title])
            font = header.font(0)
            font.setBold(True)
            header.setFont(0, font)
            self.addTopLevelItem(header)
            for key, value in rows:
                child = QTreeWidgetItem([key, value])
                child.setToolTip(1, value)
                header.addChild(child)
            header.setExpanded(True)
        self.resizeColumnToContents(0)


def build_sections(item: MediaItem, data: bytes, info: ImageInfo) -> list[tuple[str, list[tuple[str, str]]]]:
    file_rows = [
        ("Name", item.name),
        ("Location", str(item.path)),
        ("Size", human_size(len(data))),
        ("Dimensions", f"{info.width} x {info.height}"),
        ("Date", format_timestamp(item.sort_time)),
    ]
    if item.is_npi:
        meta = read_meta(item.path)
        file_rows.extend(
            [
                ("Container", "NPI"),
                ("Original format", meta.image_format.name),
                ("Container size", human_size(os.path.getsize(item.path))),
            ]
        )
    sections = [("File", file_rows), ("EXIF", read_exif_tags(info.exif))]
    if prefs().get("location_features"):
        sections.append(("Location", read_gps(info.exif)))
    return sections


class ViewerWindow(QWidget):
    def __init__(self, items: list[MediaItem], row: int, parent=None, slideshow: bool = False):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(1200, 800)
        self._items = items
        self._row = row
        self._canvas = ImageCanvas(self)
        self._canvas.step_requested.connect(self._step)
        self._panel = InfoPanel(self)
        self._panel.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._panel)
        self._slideshow = QTimer(self)
        self._slideshow.setInterval(SLIDESHOW_INTERVAL_MS)
        self._slideshow.timeout.connect(self._advance)
        self._show_row(row)
        self._title_bar_styled = False
        if slideshow:
            self._slideshow.start()
            self.showFullScreen()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._title_bar_styled:
            self._title_bar_styled = True
            style_title_bar(self, caption="#0e0e0e")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _advance(self) -> None:
        self._show_row((self._row + 1) % len(self._items))

    def _show_row(self, row: int) -> None:
        self._row = row
        item = self._items[row]
        self.setWindowTitle(f"{item.name}  ({row + 1}/{len(self._items)})")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            data = read_image_bytes(item.path)
            info = inspect_image(data)
            image = pil_to_qimage(load_oriented_rgba(io.BytesIO(data)))
            sections = build_sections(item, data, info)
        except Exception as error:
            self._canvas.clear_image()
            self._panel.populate([("Error", [("Message", str(error))])])
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._canvas.set_image(image)
        self._panel.populate(sections)

    def _step(self, delta: int) -> None:
        row = self._row + delta
        if 0 <= row < len(self._items):
            self._show_row(row)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self._step(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self._step(-1)
        elif key == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        elif key == Qt.Key.Key_F:
            self._canvas.fit(upscale=True)
        elif key == Qt.Key.Key_1:
            self._canvas.actual_size()
        elif key == Qt.Key.Key_S:
            if self._slideshow.isActive():
                self._slideshow.stop()
            else:
                self._slideshow.start()
        elif key == Qt.Key.Key_I:
            self._panel.setVisible(not self._panel.isVisible())
        else:
            super().keyPressEvent(event)
