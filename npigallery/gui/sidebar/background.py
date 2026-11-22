"""Optional picture behind the sidebar (like the Phone Link panel), loaded once and cached."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QPixmap

MAX_SIDE = 1600  # enough to stay sharp when zoomed in on a sidebar-sized window of the picture
MAX_BLUR = 24
IMAGE_FILTER = (
    "Images (*.png *.jpg *.jpeg *.jfif *.webp *.bmp *.gif *.tif *.tiff *.heic *.heif);;All files (*)"
)

MAX_ZOOM = 6.0
DEFAULT_CROP = (0.5, 0.5, 1.0)  # centre x, centre y (0..1 of the picture), zoom (1 = the largest window that fits)

RESULT_LIMIT = 10


def _reduction(blur: int) -> int:
    if blur >= 14:
        return 4
    if blur >= 6:
        return 2
    return 1


class PictureCache:
    """Decoded + blurred copies of one picture at a time, so several users (the sidebar, the window
    background) each keep their own cache instead of evicting each other's picture."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._source_key: tuple[str, float] | None = None
        self._source = None  # PIL image, already downscaled
        self._results: OrderedDict[tuple[str, float, int], QImage] = OrderedDict()
        self._pixmap_key: tuple[str, float, int] | None = None
        self._pixmap: QPixmap | None = None

    def load_source(self, path: str):
        """Decode + downscale `path` with Pillow (handles HEIC too when pillow-heif is registered)."""
        with self._lock:
            try:
                key = (path, os.path.getmtime(path))
            except OSError:
                self._source_key, self._source = None, None
                return None
            if key == self._source_key and self._source is not None:
                return self._source
            try:
                from PIL import Image, ImageOps

                with Image.open(path) as image:
                    try:
                        image.draft("RGB", (MAX_SIDE, MAX_SIDE))  # JPEG: decode at reduced size, much faster
                    except Exception:
                        pass
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((MAX_SIDE, MAX_SIDE))
                    source = image.convert("RGB")
            except Exception:
                self._source_key, self._source = None, None
                return None
            self._source_key, self._source = key, source
            self._results.clear()
            return source

    @staticmethod
    def result_key(path: str, blur: int) -> tuple[str, float, int] | None:
        blur = max(0, min(MAX_BLUR, int(blur)))
        try:
            return path, os.path.getmtime(path), blur
        except OSError:
            return None

    def cached_image(self, path: str, blur: int) -> QImage | None:
        if not path:
            return None
        key = self.result_key(path, blur)
        if key is None:
            return None
        with self._lock:
            image = self._results.get(key)
            if image is not None:
                self._results.move_to_end(key)
            return image

    def render_image(self, path: str, blur: int) -> QImage | None:
        if not path:
            return None
        blur = max(0, min(MAX_BLUR, int(blur)))
        cached = self.cached_image(path, blur)
        if cached is not None:
            return cached
        source = self.load_source(path)
        if source is None:
            return None
        with self._lock:
            key = (path, self._source_key[1], blur)  # type: ignore[index]
        image = source
        if blur > 0:
            from PIL import ImageFilter

            factor = _reduction(blur)
            if factor > 1:
                image = image.reduce(factor)
            image = image.filter(ImageFilter.GaussianBlur(blur * max(image.size) / 900))
        data = image.tobytes("raw", "RGB")
        qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
        with self._lock:
            self._results[key] = qimage
            self._results.move_to_end(key)
            while len(self._results) > RESULT_LIMIT:
                self._results.popitem(last=False)
        return qimage

    def pixmap(self, path: str, blur: int) -> QPixmap | None:
        """The picture at `path`, blurred by `blur` (0..MAX_BLUR), or None if it can't be read."""
        image = self.render_image(path, blur)
        if image is None:
            return None
        key = self.result_key(path, blur)
        if key is not None and key == self._pixmap_key and self._pixmap is not None:
            return self._pixmap
        pixmap = QPixmap.fromImage(image)
        self._pixmap_key, self._pixmap = key, pixmap
        return pixmap


_shared = PictureCache()  # the sidebar's picture, and the crop dialog's source


def cached_image(path: str, blur: int) -> QImage | None:
    return _shared.cached_image(path, blur)


def render_image(path: str, blur: int) -> QImage | None:
    return _shared.render_image(path, blur)


def background_pixmap(path: str, blur: int) -> QPixmap | None:
    """The picture at `path`, blurred by `blur` (0..MAX_BLUR), or None if it can't be read."""
    return _shared.pixmap(path, blur)


class BackgroundRenderer(QObject):
    rendered = Signal(str, int, QImage)

    def __init__(self, parent=None, cache: PictureCache | None = None):
        super().__init__(parent)
        self._cache = cache or _shared
        self._guard = threading.Lock()
        self._pending: tuple[str, int] | None = None
        self._busy = False

    def request(self, path: str, blur: int) -> None:
        with self._guard:
            self._pending = (path, blur)
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        while True:
            with self._guard:
                job, self._pending = self._pending, None
                if job is None:
                    self._busy = False
                    return
            image = self._cache.render_image(*job)
            if image is not None:
                self.rendered.emit(job[0], job[1], image)


def source_pixmap(path: str) -> QPixmap | None:
    """The picture itself (no blur), for the crop dialog."""
    return background_pixmap(path, 0)


def source_image(path: str):
    """The decoded, downscaled PIL picture (no blur), or None. The accent colour is taken from it."""
    return _shared.load_source(path) if path else None


def crop_region(
    width: float, height: float, aspect: float, cx: float, cy: float, zoom: float
) -> tuple[float, float, float, float]:
    """The part of a `width` x `height` picture that is shown in a panel of the given aspect (w / h).

    At zoom 1 it is the biggest window of that shape that fits inside the picture; higher zoom makes
    it smaller. (cx, cy) is its centre as a fraction of the picture; the window is kept inside it.
    Because it is described by centre + zoom, a panel that later gets a different shape still shows
    the same spot, filled the same way.
    """
    aspect = max(aspect, 0.01)
    zoom = max(1.0, min(MAX_ZOOM, zoom))
    w = min(width, height * aspect) / zoom
    h = w / aspect
    x = max(0.0, min(width - w, cx * width - w / 2))
    y = max(0.0, min(height - h, cy * height - h / 2))
    return x, y, w, h


def parse_crop(text: str) -> tuple[float, float, float]:
    try:
        cx, cy, zoom = (float(part) for part in str(text).split(","))
    except ValueError:
        return DEFAULT_CROP
    return max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy)), max(1.0, min(MAX_ZOOM, zoom))


def format_crop(crop: tuple[float, float, float]) -> str:
    return f"{crop[0]:.4f},{crop[1]:.4f},{crop[2]:.3f}"