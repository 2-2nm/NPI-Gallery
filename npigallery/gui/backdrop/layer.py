"""One picture, framed to the whole main window, that every part of the window paints from.

The window root, the photo grid and the settings edge fades all ask `backdrop().paint(...)` for the
piece that lies behind them, so the picture is seamless across the floating cards and does not
scroll with the photos. The framed picture is built once per window size / setting and kept as a
single pixmap, so painting it is a plain blit and the opaque photo viewport keeps its fast path.

What lies behind the window is one of these (the "window_bg_mode" preference):
  none     the plain window colour (nothing is painted here)
  picture  a picture of the user's choice, framed to the window
  mica     the desktop wallpaper, blurred and tinted, laid out in screen coordinates so it lines up with
           the desktop behind the window; drawn by the app itself, so it needs nothing from Windows
  acrylic_manual  the look of Windows' Acrylic drawn by the app: the wallpaper behind the window, blurred
           less than Mica, with a little extra colour, the tint and a fine grain. Needs nothing from Windows.
  acrylic  the system Acrylic: Windows blurs what is behind the window (other windows too) and the app
           paints only the tint. Fixed blur, no cost. Offered only while Transparency effects is on in Windows;
           without it the window shows the manual Acrylic.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter, QPixmap, QScreen
from PySide6.QtWidgets import QWidget

from ..sidebar.background import BackgroundRenderer, PictureCache, crop_region, parse_crop
from ..theme import theme
from ..theme.preferences import prefs
from . import mica_render, system
from .wallpaper import MicaSource

SETTLE_MS = 70  # after the last resize event the framed picture is rebuilt once at the final size
WATCHED_KEYS = (
    "window_bg_mode", "window_bg_path", "window_bg_crop", "window_bg_dim", "window_bg_blur",
    "window_mica_blur", "window_acrylic_blur",
)
NOISE_OPACITY = 0.04  # how strongly the grain shows on the manual Acrylic

NONE, PICTURE, MICA, ACRYLIC_MANUAL, ACRYLIC = "none", "picture", "mica", "acrylic_manual", "acrylic"
WALLPAPER_KINDS = (MICA, ACRYLIC_MANUAL)  # drawn by the app from the desktop wallpaper, in screen coordinates


def compose(picture: QPixmap, crop: tuple[float, float, float], dim: float, tone: QColor, size: QSize) -> QPixmap:
    """`picture` framed to `size` (device pixels) by `crop`, with `tone` laid over it at strength `dim`."""
    width, height = max(1, size.width()), max(1, size.height())
    x, y, crop_width, crop_height = crop_region(picture.width(), picture.height(), width / height, *crop)
    framed = QPixmap(width, height)
    painter = QPainter(framed)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(0, 0, width, height), picture, QRectF(x, y, crop_width, crop_height))
    cover = QColor(tone)
    cover.setAlphaF(max(0.0, min(1.0, dim)))
    painter.fillRect(framed.rect(), cover)
    painter.end()
    return framed


class Backdrop(QObject):
    changed = Signal()
    availability_changed = Signal()  # system Acrylic can now be offered / can no longer be offered

    def __init__(self) -> None:
        super().__init__()
        self._cache = PictureCache()  # its own cache: the sidebar picture and this one never evict each other
        self._renderer = BackgroundRenderer(self, self._cache)
        self._renderer.rendered.connect(self._on_rendered)
        self._root: QWidget | None = None
        self._picture: QPixmap | None = None  # the blurred picture, before it is framed
        self._crop = parse_crop(prefs().get("window_bg_crop"))
        self._dim = prefs().get("window_bg_dim") / 100
        self._mode = prefs().get("window_bg_mode")
        self._mica_blur = prefs().get("window_mica_blur")
        self._acrylic_blur = prefs().get("window_acrylic_blur")
        self._noise: QPixmap | None = None  # the grain tile of the manual Acrylic, made once
        self._framed: QPixmap | None = None
        self._framed_key: tuple | None = None
        self._framed_size = QSize()
        self._revision = 0
        self._mica = MicaSource(self)
        self._mica.ready.connect(self._on_mica_ready)
        self._mica_pixmap: QPixmap | None = None  # the blurred wallpaper of the window's screen, tinted
        self._mica_key: tuple | None = None
        self._origin = QPoint()  # where the window root is on the screen; Mica lines up with the desktop by it
        self._acrylic_ok = system.acrylic_available()  # Windows 11 and Transparency effects is on
        self._acrylic_failed = False  # Windows refused the effect for this window
        self._kind = NONE
        self._update_kind()
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._on_settled)
        prefs().changed.connect(self._on_preference_changed)
        theme.signals().changed.connect(self._on_theme_changed)
        self._reload()

    # // the window it is painted behind

    def attach(self, root: QWidget) -> None:
        self._root = root

    def canvas_resized(self) -> None:
        """The window root changed size: keep painting the old framing (scaled) until the resizing stops."""
        if self._framed is not None:
            self._settle.start()

    def window_moved(self) -> None:
        """The window moved: Mica shows the part of the wallpaper that lies behind it, so it must follow."""
        root = self._root
        if root is None or self._kind not in WALLPAPER_KINDS:
            return
        origin = root.mapToGlobal(QPoint(0, 0))
        if origin != self._origin:
            self._origin = origin
            self.changed.emit()

    def kind(self) -> str:
        """What is behind the window right now: NONE, PICTURE, MICA or ACRYLIC."""
        return self._kind

    def active(self) -> bool:
        """A picture (or the blurred wallpaper) is ready to paint behind the window."""
        if self._root is None:
            return False
        if self._kind in WALLPAPER_KINDS:
            return self._current_mica() is not None
        return self._kind == PICTURE and self._picture is not None

    def translucent(self) -> bool:
        """The window is painted with a see-through tint (system Acrylic), so nothing may be filled opaque over it."""
        return self._kind == ACRYLIC

    # // what the user can pick

    def supported_modes(self) -> list[str]:
        modes = [NONE, PICTURE]
        if system.IS_WINDOWS:
            modes += [MICA, ACRYLIC_MANUAL]
            if system.translucent_supported():
                modes.append(ACRYLIC)
        return modes

    def disabled_modes(self) -> list[str]:
        """Modes that are listed but cannot be chosen now (the system Acrylic while Transparency effects is off)."""
        if system.translucent_supported() and not self.acrylic_available():
            return [ACRYLIC]
        return []

# a poor lady

    def acrylic_available(self) -> bool:
        return self._acrylic_ok and not self._acrylic_failed

    def _update_kind(self) -> None:
        mode = self._mode
        if mode == PICTURE:
            self._kind = PICTURE
        elif mode in (MICA, ACRYLIC_MANUAL, ACRYLIC) and system.IS_WINDOWS:
            if mode == ACRYLIC:  # the system Acrylic when Windows gives it, otherwise the same look drawn by the app
                self._kind = ACRYLIC if self.acrylic_available() else ACRYLIC_MANUAL
            else:
                self._kind = mode
        else:
            self._kind = NONE

    # // the native window

    def sync_window(self) -> None:
        """Turn the system Acrylic of the native window on or off to match the mode.

        Call once the window is shown, and again whenever Windows may have dropped it (a frame refresh).
        """
        root = self._root
        if root is None or not system.IS_WINDOWS:
            return
        window = root.window()
        if window is None or not window.isVisible():
            return
        if system.apply_system_acrylic(int(window.winId()), self._kind == ACRYLIC) or self._kind != ACRYLIC:
            return
        self._acrylic_failed = True  # no Acrylic for this window: show the manual one instead and stop offering it
        self._update_kind()
        self.availability_changed.emit()
        self.changed.emit()

    def refresh_system(self) -> None:
        """Windows settings may have changed (Transparency effects, the wallpaper): read them again."""
        available = system.acrylic_available()
        if available != self._acrylic_ok:
            self._acrylic_ok = available
            self._acrylic_failed = False
            self._update_kind()
            self.sync_window()
            self.availability_changed.emit()
            self.changed.emit()
        if self._mica.refresh_wallpaper() and self._kind in WALLPAPER_KINDS:
            self.changed.emit()

    # // painting

    def paint(self, painter: QPainter, widget: QWidget, rect: QRect) -> bool:
        """Paint the part of the picture that lies behind `rect` (in `widget` coordinates).

        Returns False when there is no picture, so the caller can fill with its own color instead.
        """
        root = self._root
        if root is None or (widget is not root and not root.isAncestorOf(widget)):
            return False
        if self._kind == ACRYLIC:
            return self._paint_tint(painter, rect)
        if self._kind in WALLPAPER_KINDS:
            return self._paint_mica(painter, widget, rect)
        framed = self._current()
        if framed is None:
            return False
        origin = widget.mapTo(root, rect.topLeft())
        scale_x = framed.width() / max(1, root.width())
        scale_y = framed.height() / max(1, root.height())
        source = QRectF(origin.x() * scale_x, origin.y() * scale_y, rect.width() * scale_x, rect.height() * scale_y)
        painter.drawPixmap(QRectF(rect), framed, source)
        return True

    def _paint_tint(self, painter: QPainter, rect: QRect) -> bool:
        """Acrylic: the system draws the blur, the app lays only the tint over it.

        The tint replaces what is under it (Source) instead of blending, because there is nothing opaque
        under it to blend with: the window is translucent and the system shows through the see-through part.
        """
        tint = QColor(theme.palette().chrome)
        tint.setAlphaF(max(0.0, min(1.0, self._dim)))
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(rect, tint)
        painter.restore()
        return True

    def _paint_mica(self, painter: QPainter, widget: QWidget, rect: QRect) -> bool:
        framed = self._current_mica()
        screen = self._screen()
        if framed is None or screen is None:
            return False
        geometry = screen.geometry()
        scale_x = framed.width() / max(1, geometry.width())
        scale_y = framed.height() / max(1, geometry.height())
        origin = widget.mapToGlobal(rect.topLeft())
        source = (
            (origin.x() - geometry.x()) * scale_x,
            (origin.y() - geometry.y()) * scale_y,
            rect.width() * scale_x,
            rect.height() * scale_y,
        )
        target = (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
        painter.fillRect(rect, QColor(theme.palette().chrome))  # under any part that hangs off the screen
        clipped = mica_render.clip_source(source, target, (0.0, 0.0, float(framed.width()), float(framed.height())))
        if clipped is not None:
            (sx, sy, sw, sh), (tx, ty, tw, th) = clipped
            painter.drawPixmap(QRectF(tx, ty, tw, th), framed, QRectF(sx, sy, sw, sh))
        return True

    def _screen(self) -> QScreen | None:
        root = self._root
        return (root.screen() if root is not None else None) or QGuiApplication.primaryScreen()

    def _canvas_pixels(self) -> QSize:
        root = self._root
        ratio = root.devicePixelRatioF()
        return QSize(max(1, round(root.width() * ratio)), max(1, round(root.height() * ratio)))

    def _current(self) -> QPixmap | None:
        picture = self._picture
        if picture is None or self._root is None:
            return None
        tone = QColor(theme.palette().chrome)
        key = (picture.cacheKey(), self._crop, self._dim, tone.rgba())
        size = self._canvas_pixels()
        stale = self._framed is None or key != self._framed_key
        if stale or (size != self._framed_size and not self._settle.isActive()):
            self._framed = compose(picture, self._crop, self._dim, tone, size)
            self._framed_key, self._framed_size = key, size
            self._revision += 1
        return self._framed

    def _current_mica(self) -> QPixmap | None:
        """The blurred wallpaper of the window's screen, tinted, at that screen's own pixel size.

        Mica is the wallpaper blurred as much as the user likes. The manual Acrylic blurs it less, adds some
        colour, lays the tint over it and finishes with the fine grain Windows puts on its acrylic.
        """
        screen = self._screen()
        if screen is None or self._root is None:
            return None
        acrylic = self._kind == ACRYLIC_MANUAL
        image = self._mica.image(
            screen, self._acrylic_blur if acrylic else self._mica_blur, mica_render.ACRYLIC_SATURATION if acrylic else 1.0
        )
        if image is None:
            return None
        geometry, ratio = screen.geometry(), screen.devicePixelRatio()
        size = QSize(max(1, round(geometry.width() * ratio)), max(1, round(geometry.height() * ratio)))
        tone = QColor(theme.palette().chrome)
        key = (image.cacheKey(), self._dim, tone.rgba(), size, acrylic)
        if self._mica_pixmap is None or key != self._mica_key:
            pixmap = QPixmap(size)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(0, 0, size.width(), size.height()), image)
            tint = QColor(tone)
            tint.setAlphaF(max(0.0, min(1.0, self._dim)))
            painter.fillRect(pixmap.rect(), tint)
            if acrylic:
                painter.setOpacity(NOISE_OPACITY)
                painter.fillRect(pixmap.rect(), QBrush(self._grain()))
            painter.end()
            self._mica_pixmap, self._mica_key = pixmap, key
            self._revision += 1
        return self._mica_pixmap

    def _grain(self) -> QPixmap:
        if self._noise is None:
            tile = mica_render.noise_tile()
            image = QImage(tile.tobytes(), tile.width, tile.height, tile.width, QImage.Format.Format_Grayscale8).copy()
            self._noise = QPixmap.fromImage(image)
        return self._noise

    def revision(self) -> tuple:
        """Changes whenever the picture is rebuilt (or, in Mica, the window moves), so a copy of a piece of it knows when to be redrawn."""
        if self._kind in WALLPAPER_KINDS:
            self._current_mica()
        else:
            self._current()
        return (self._revision, self._origin.x(), self._origin.y())

    def _on_settled(self) -> None:
        if self._framed is not None and self._root is not None and self._canvas_pixels() != self._framed_size:
            self.changed.emit()

    # // the picture and its settings

    def _reload(self) -> None:
        store = prefs()
        path, blur = store.get("window_bg_path"), store.get("window_bg_blur")
        if store.get("window_bg_mode") != "picture" or not path:
            self._picture = None
            return
        image = self._cache.cached_image(path, blur)
        if image is not None:
            self._picture = QPixmap.fromImage(image)
        else:
            self._renderer.request(path, blur)  # the previous picture stays up until the new one is ready

    def _on_rendered(self, path: str, blur: int, image) -> None:
        store = prefs()
        if store.get("window_bg_mode") == "picture" and (path, blur) == (
            store.get("window_bg_path"),
            store.get("window_bg_blur"),
        ):
            self._picture = QPixmap.fromImage(image)
            self.changed.emit()

    def _on_mica_ready(self) -> None:
        if self._kind in WALLPAPER_KINDS:
            self.changed.emit()

    def _on_preference_changed(self, key: str) -> None:
        if key not in WATCHED_KEYS:
            return
        store = prefs()
        self._crop = parse_crop(store.get("window_bg_crop"))
        self._dim = store.get("window_bg_dim") / 100
        self._mode = store.get("window_bg_mode")
        self._mica_blur = store.get("window_mica_blur")
        self._acrylic_blur = store.get("window_acrylic_blur")
        if key in ("window_bg_mode", "window_bg_path", "window_bg_blur"):
            self._reload()
        before = self._kind
        self._update_kind()
        if key == "window_bg_mode" or self._kind != before:
            self._origin = self._root.mapToGlobal(QPoint(0, 0)) if self._root is not None else QPoint()
            self.sync_window()
        self.changed.emit()

    def _on_theme_changed(self, *_args) -> None:
        self.changed.emit()

    # // for the crop dialog, which has its own pictures and must not disturb the one on screen

    def source_pixmap(self, path: str) -> QPixmap | None:
        return self._cache.pixmap(path, 0)

    def blurred_pixmap(self, path: str, blur: int) -> QPixmap | None:
        return self._cache.pixmap(path, blur)


_instance: Backdrop | None = None


def backdrop() -> Backdrop:
    global _instance
    if _instance is None:
        _instance = Backdrop()
    return _instance