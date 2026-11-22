"""Frameless-window plumbing for the viewer, mirroring the main window.

Windows keeps its native frame (animations, Aero snap, resize borders) and only drops the native
title bar by answering WM_NCCALCSIZE; the viewer draws its own caption buttons. Other platforms
use a frameless window with edge grips that hand resizing to the window manager.
"""

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget

IS_WINDOWS = sys.platform == "win32"
GRIP = 6

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED


if IS_WINDOWS:

    class _NcCalcSizeParams(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    def _configure_api() -> None:
        user32 = ctypes.windll.user32
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.IsZoomed.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ScreenToClient.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        if hasattr(user32, "GetDpiForWindow") and hasattr(user32, "GetSystemMetricsForDpi"):
            user32.GetDpiForWindow.argtypes = [wintypes.HWND]
            user32.GetDpiForWindow.restype = wintypes.UINT
            user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
            user32.GetSystemMetricsForDpi.restype = ctypes.c_int

    def _maximized_frame_insets(hwnd: int) -> tuple[int, int]:
        user32 = ctypes.windll.user32
        if hasattr(user32, "GetDpiForWindow") and hasattr(user32, "GetSystemMetricsForDpi"):
            dpi = user32.GetDpiForWindow(hwnd)
            if dpi:
                padded = user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
                return (
                    user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi) + padded,
                    user32.GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi) + padded,
                )
        padded = user32.GetSystemMetrics(SM_CXPADDEDBORDER)
        return (
            user32.GetSystemMetrics(SM_CXSIZEFRAME) + padded,
            user32.GetSystemMetrics(SM_CYSIZEFRAME) + padded,
        )

    _configure_api()


ROUND_RADIUS = 12
WIN11_BUILD = 22000
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2


def round_corners(window: QWidget) -> None:
    """Rounded window corners. Windows 11 does it itself once asked; on Windows 10 the refresh
    below clips the window to a rounded region. Call from showEvent."""
    if not IS_WINDOWS:
        return
    if sys.getwindowsversion().build >= WIN11_BUILD:
        try:
            value = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(int(window.winId())),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass
        return
    refresh_corner_region(window)


def refresh_corner_region(window: QWidget) -> None:
    """Windows 10 only: re-clip to a rounded region after a resize or a state change."""
    if not IS_WINDOWS or sys.getwindowsversion().build >= WIN11_BUILD:
        return
    try:
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        hwnd = wintypes.HWND(int(window.winId()))
        if window.isMaximized() or window.isFullScreen():
            user32.SetWindowRgn(hwnd, None, True)
            return
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        diameter = round(ROUND_RADIUS * 2 * window.devicePixelRatioF())
        region = gdi32.CreateRoundRectRgn(
            0, 0, rect.right - rect.left + 1, rect.bottom - rect.top + 1, diameter, diameter
        )
        user32.SetWindowRgn(hwnd, region, True)  # the window owns the region from here on
    except Exception:
        pass


def apply_frame(window: QWidget) -> None:
    """Make Windows re-read the frame so the native title bar disappears. Call once from showEvent."""
    if IS_WINDOWS:
        ctypes.windll.user32.SetWindowPos(int(window.winId()), 0, 0, 0, 0, 0, SWP_FLAGS)


def handle_native_event(
    window: QWidget,
    event_type,
    message,
    is_caption_at: Callable[[QPoint], bool],
    is_client_at: Callable[[QPoint], bool] | None = None,
) -> tuple[bool, int] | None:
    """Answer WM_NCCALCSIZE / WM_NCHITTEST for `window`; None means "not ours, use the default".
    `is_caption_at` takes a point in window (logical) coordinates and says whether a drag from
    there should move the window. `is_client_at` says the point must stay an ordinary click, even on
    the edge (the close button reaching the corner): the resize grips must not take it."""
    if not IS_WINDOWS or bytes(event_type) != b"windows_generic_MSG":
        return None
    msg = wintypes.MSG.from_address(int(message))
    if msg.message == WM_NCCALCSIZE and msg.wParam:
        # Client area = whole window: no native title bar, native frame behaviour kept.
        # IsZoomed is the live state; Qt's isMaximized() lags while a maximized window is restored.
        if bool(ctypes.windll.user32.IsZoomed(msg.hWnd)):
            frame_x, frame_y = _maximized_frame_insets(msg.hWnd)
            rect = ctypes.cast(msg.lParam, ctypes.POINTER(_NcCalcSizeParams)).contents.rgrc[0]
            rect.left += frame_x
            rect.top += frame_y
            rect.right -= frame_x
            rect.bottom -= frame_y
        return True, 0
    if msg.message == WM_NCHITTEST:
        return True, _hit_test(window, msg, is_caption_at, is_client_at)
    return None


def _hit_test(
    window: QWidget,
    msg,
    is_caption_at: Callable[[QPoint], bool],
    is_client_at: Callable[[QPoint], bool] | None = None,
) -> int:
    user32 = ctypes.windll.user32
    screen_x = ctypes.c_short(msg.lParam & 0xFFFF).value
    screen_y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
    ratio = window.devicePixelRatioF()
    maximized = bool(user32.IsZoomed(msg.hWnd))
    client = wintypes.POINT(screen_x, screen_y)
    user32.ScreenToClient(msg.hWnd, ctypes.byref(client))
    point = QPoint(round(client.x / ratio), round(client.y / ratio))
    if is_client_at is not None and is_client_at(point):
        return HTCLIENT
    if not maximized and not window.isFullScreen():
        window_rect = wintypes.RECT()
        user32.GetWindowRect(msg.hWnd, ctypes.byref(window_rect))
        x = screen_x - window_rect.left
        y = screen_y - window_rect.top
        width = window_rect.right - window_rect.left
        height = window_rect.bottom - window_rect.top
        border = max(1, round(GRIP * ratio))
        left, right = x < border, x >= width - border
        top, bottom = y < border, y >= height - border
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
    if is_caption_at(point):
        return HTCAPTION
    return HTCLIENT


# -------------------------------------
# // frameless fallback (macOS / Linux)


class _EdgeGrip(QWidget):
    def __init__(self, window: QWidget, edges: Qt.Edge, cursor: Qt.CursorShape):
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)


def build_grips(window: QWidget) -> list[tuple[_EdgeGrip, Qt.Edge]]:
    if IS_WINDOWS:
        return []
    edge, cursor = Qt.Edge, Qt.CursorShape
    specs = [
        (edge.LeftEdge, cursor.SizeHorCursor),
        (edge.RightEdge, cursor.SizeHorCursor),
        (edge.TopEdge, cursor.SizeVerCursor),
        (edge.BottomEdge, cursor.SizeVerCursor),
        (edge.TopEdge | edge.LeftEdge, cursor.SizeFDiagCursor),
        (edge.BottomEdge | edge.RightEdge, cursor.SizeFDiagCursor),
        (edge.TopEdge | edge.RightEdge, cursor.SizeBDiagCursor),
        (edge.BottomEdge | edge.LeftEdge, cursor.SizeBDiagCursor),
    ]
    grips = [(_EdgeGrip(window, edges, shape), edges) for edges, shape in specs]
    place_grips(window, grips)
    return grips


def place_grips(window: QWidget, grips) -> None:
    width, height, g, c = window.width(), window.height(), GRIP, GRIP * 2
    edge = Qt.Edge
    for grip, edges in grips:
        if window.isMaximized() or window.isFullScreen():
            grip.hide()
            continue
        left, right = bool(edges & edge.LeftEdge), bool(edges & edge.RightEdge)
        top, bottom = bool(edges & edge.TopEdge), bool(edges & edge.BottomEdge)
        corner = (left or right) and (top or bottom)
        x = 0 if left else (width - g if right else c)
        y = 0 if top else (height - g if bottom else c)
        w = (c if corner else g) if (left or right) else width - 2 * c
        h = (c if corner else g) if (top or bottom) else height - 2 * c
        grip.setGeometry(x, y, w, h)
        grip.show()
        grip.raise_()