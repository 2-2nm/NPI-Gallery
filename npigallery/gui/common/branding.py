"""App icon, logo and Windows shell integration (taskbar identity, dark title bar)."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

APP_USER_MODEL_ID = "NpiGallery.Desktop"
ASSETS = Path(__file__).resolve().parents[2] / "assets"

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def app_icon() -> QIcon:
    for name in ("npi-gallery.ico", "icon.png"):
        icon = QIcon(str(ASSETS / name))
        if icon.availableSizes():
            return icon
    return QIcon()


def logo_pixmap(size: int) -> QPixmap:
    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    ratio = max(1.0, screen.devicePixelRatio()) if screen else 1.0
    pixel = int(size * ratio)
    pixmap = QPixmap(str(ASSETS / "icon.png"))
    if pixmap.isNull():
        return pixmap
    pixmap = pixmap.scaled(
        pixel, pixel, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def set_app_user_model_id() -> None:
    """Give the process its own taskbar identity."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _colorref(color: str) -> int:
    value = color.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return red | (green << 8) | (blue << 16)


def style_title_bar(widget: QWidget, caption: str = "#202020", text: str = "#ffffff", dark: bool = True) -> None:
    """Title bar that blends with the app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi

        def apply(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

        apply(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        apply(DWMWA_CAPTION_COLOR, _colorref(caption))
        apply(DWMWA_BORDER_COLOR, _colorref(caption))
        apply(DWMWA_TEXT_COLOR, _colorref(text))
    except Exception:
        pass
