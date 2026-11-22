import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QWidget

from ..backdrop import backdrop
from ..common.branding import style_title_bar
from ..theme import theme
from ..viewer.frame import refresh_corner_region, round_corners
from .top_bar import TOP_BAR_HEIGHT


GRIP = 6
IS_WINDOWS = sys.platform == "win32"
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_SETTINGCHANGE = 0x001A
SPI_SETDESKWALLPAPER = 0x0014
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED


class _NcCalcSizeParams(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


def _configure_win32_api() -> None:
    if not IS_WINDOWS:
        return
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


_configure_win32_api()


def style_title_bar_for_theme(widget: QWidget) -> None:
    p = theme.palette()
    style_title_bar(widget, caption=p.chrome, text=p.text, dark=p.dark)


class _EdgeGrip(QWidget):
    def __init__(self, window: QMainWindow, edges: Qt.Edge, cursor: Qt.CursorShape):
        super().__init__(window)
        self._window = window
        self._edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)


class FramelessMixin:
    # // frameless window support

    def _build_grips(self) -> list[tuple[_EdgeGrip, Qt.Edge]]:
        if IS_WINDOWS:
            return []
        edge = Qt.Edge
        cursor = Qt.CursorShape
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
        grips = [(_EdgeGrip(self, edges, shape), edges) for edges, shape in specs]
        self._place_grips(grips)
        return grips

    def _place_grips(self, grips=None) -> None:
        grips = grips if grips is not None else self._grips
        width, height, g, c = self.width(), self.height(), GRIP, GRIP * 2
        edge = Qt.Edge
        for grip, edges in grips:
            if self.isMaximized() or self.isFullScreen():
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_grips"):
            self._place_grips()
        refresh_corner_region(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if IS_WINDOWS and not getattr(self, "_frame_applied", False):
            self._frame_applied = True
            ctypes.windll.user32.SetWindowPos(int(self.winId()), 0, 0, 0, 0, 0, SWP_FLAGS)
            round_corners(self)

    def nativeEvent(self, event_type, message):
        if not IS_WINDOWS or bytes(event_type) != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            # Client area = whole window: no native title bar, native frame behaviour kept.
            # IsZoomed is the live state. Qt's isMaximized() lags behind while Windows restores a
            # maximized window (drag from the top), which left a stale inset strip at the top.
            if bool(ctypes.windll.user32.IsZoomed(msg.hWnd)):
                frame_x, frame_y = _maximized_frame_insets(msg.hWnd)
                rect = ctypes.cast(msg.lParam, ctypes.POINTER(_NcCalcSizeParams)).contents.rgrc[0]
                rect.left += frame_x
                rect.top += frame_y
                rect.right -= frame_x
                rect.bottom -= frame_y
            return True, 0
        if msg.message == WM_NCHITTEST:
            return True, self._hit_test(msg)
        if msg.message == WM_SETTINGCHANGE and msg.wParam == SPI_SETDESKWALLPAPER:
            self._schedule_backdrop_refresh()  # a new wallpaper: Mica is drawn from it
        if msg.message == WM_SETTINGCHANGE and msg.lParam:
            # "ImmersiveColorSet" is sent when the Windows accent colour or light / dark mode changes.
            try:
                topic = ctypes.wstring_at(msg.lParam)
            except OSError:
                topic = ""
            if topic == "ImmersiveColorSet":
                self._schedule_system_colors_refresh()
        return super().nativeEvent(event_type, message)

    def _schedule_backdrop_refresh(self) -> None:
        timer = getattr(self, "_backdrop_timer", None)
        if timer is None:
            timer = self._backdrop_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(400)  # the new picture file is still being written when the message arrives
            timer.timeout.connect(lambda: backdrop().refresh_system())
        timer.start()

    def _schedule_system_colors_refresh(self) -> None:
        timer = getattr(self, "_colors_timer", None)
        if timer is None:
            timer = self._colors_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(300)  # Windows sends several messages; the registry is settled after the last
            timer.timeout.connect(self._on_system_colors_changed)
        timer.start()

    def _hit_test(self, msg) -> int:
        user32 = ctypes.windll.user32
        screen_x = ctypes.c_short(msg.lParam & 0xFFFF).value
        screen_y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        ratio = self.devicePixelRatioF()
        maximized = bool(user32.IsZoomed(msg.hWnd))
        if self._over_close_button(msg, ratio):
            return HTCLIENT
        if not maximized and not self.isFullScreen():
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
        client_pt = wintypes.POINT(screen_x, screen_y)
        user32.ScreenToClient(msg.hWnd, ctypes.byref(client_pt))
        if 0 <= client_pt.y < round(TOP_BAR_HEIGHT * ratio):
            child = self.childAt(QPoint(round(client_pt.x / ratio), round(client_pt.y / ratio)))
            if self._is_drag_region(child):
                return HTCAPTION
        return HTCLIENT

    def _over_close_button(self, msg, ratio: float) -> bool:
        """True when the pointer is anywhere on the close button, including its stretch to the corner."""
        button = self._top_bar.close_button
        if not button.isVisible():
            return False
        point = wintypes.POINT(
            ctypes.c_short(msg.lParam & 0xFFFF).value, ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        )
        ctypes.windll.user32.ScreenToClient(msg.hWnd, ctypes.byref(point))
        local = self.mapFromGlobal(button.mapToGlobal(QPoint(0, 0)))
        origin_x, origin_y = round(local.x() * ratio), round(local.y() * ratio)
        width, height = round(button.width() * ratio), round(button.height() * ratio)
        # Window-client coordinates can extend past the widget by the grip: allow the full corner.
        return origin_x <= point.x and origin_y - round(GRIP * ratio) <= point.y < origin_y + height and point.x < origin_x + width + round(GRIP * ratio)

    def _is_drag_region(self, widget: QWidget | None) -> bool:
        if widget is None or widget is self.centralWidget() or widget is self._top_bar:
            return True
        return isinstance(widget, QLabel) or bool(widget.property("dragRegion"))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # restoreGeometry() fires this during __init__, before the widgets exist.
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_grips"):
            self._top_bar.sync_maximized()
            self._place_grips()
            if IS_WINDOWS:
                # Re-read the frame once the new state has settled so no stale inset is left over.
                QTimer.singleShot(0, self._refresh_frame)

    def _refresh_frame(self) -> None:
        if IS_WINDOWS and self.isVisible():
            ctypes.windll.user32.SetWindowPos(int(self.winId()), 0, 0, 0, 0, 0, SWP_FLAGS)
            refresh_corner_region(self)
            backdrop().sync_window()  # a frame refresh can drop the system Acrylic of the window