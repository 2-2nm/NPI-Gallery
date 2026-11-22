"""The blurred desktop wallpaper for Mica mode, built off the UI thread.

`MicaSource.image(screen, blur, saturation)` returns the picture for that screen at once when it is ready, and
otherwise starts building it (the previous picture stays up until the new one is ready) and emits
`ready` when it is done.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QScreen

from . import mica_render, system

CACHE_SIZE = 4  # finished pictures kept: a couple of screens, or a blur the user just left


class MicaSource(QObject):
    ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._guard = threading.Lock()
        self._wallpaper: system.DesktopWallpaper | None = None
        self._cache: dict[tuple, QImage] = {}
        self._latest: QImage | None = None
        self._pending: tuple | None = None
        self._busy = False

    # // the wallpaper itself

    def refresh_wallpaper(self) -> bool:
        """Re-read how the desktop shows its wallpaper; True when it is not the one used so far."""
        current = system.desktop_wallpaper()
        if current == self._wallpaper:
            return False
        self._wallpaper = current
        return True

    # // pictures

    def image(self, screen: QScreen, blur: int, saturation: float = 1.0) -> QImage | None:
        if self._wallpaper is None:
            self._wallpaper = system.desktop_wallpaper()
        wallpaper = self._wallpaper
        if wallpaper is None:
            return None
        job = self._job(wallpaper, screen, blur, saturation)
        with self._guard:
            found = self._cache.get(job)
        if found is not None:
            self._latest = found
            return found
        self._request(job)
        return self._latest

    @staticmethod
    def _job(wallpaper: system.DesktopWallpaper, screen: QScreen, blur: int, saturation: float) -> tuple:
        ratio = screen.devicePixelRatio()
        geometry = screen.geometry()
        pixels = (max(1, round(geometry.width() * ratio)), max(1, round(geometry.height() * ratio)))
        canvas, region = pixels, (0.0, 0.0, 1.0, 1.0)
        if wallpaper.style == mica_render.SPAN:
            desktop = screen.virtualGeometry()
            canvas = (max(1, round(desktop.width() * ratio)), max(1, round(desktop.height() * ratio)))
            region = (
                (geometry.x() - desktop.x()) / max(1, desktop.width()),
                (geometry.y() - desktop.y()) / max(1, desktop.height()),
                geometry.width() / max(1, desktop.width()),
                geometry.height() / max(1, desktop.height()),
            )
        return (wallpaper, canvas, tuple(round(part, 4) for part in region), int(blur), round(saturation, 3))

    def _request(self, job: tuple) -> None:
        with self._guard:
            self._pending = job
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
            image = self._build(job)
            if image is None:
                continue
            with self._guard:
                self._cache[job] = image
                while len(self._cache) > CACHE_SIZE:
                    self._cache.pop(next(iter(self._cache)))
            self.ready.emit()

    @staticmethod
    def _build(job: tuple) -> QImage | None:
        wallpaper, canvas, region, blur, saturation = job
        try:
            picture = mica_render.render(wallpaper.path, wallpaper.style, canvas, region, wallpaper.fill, blur, saturation)
            return QImage(picture.tobytes(), picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()
        except Exception:  # a broken picture must not kill the worker: the next request tries again
            return None