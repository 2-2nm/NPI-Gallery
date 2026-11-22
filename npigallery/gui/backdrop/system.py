"""Windows plumbing for the window backdrop (everything here is a no-op elsewhere).

`acrylic_available` tells the settings when the system Acrylic can be offered. Mica and the manual Acrylic are
drawn by the app itself from the desktop wallpaper (see mica_render.py), so they need nothing from here but
the wallpaper's settings.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

from .mica_render import style_from_registry

IS_WINDOWS = sys.platform == "win32"

WIN11_BUILD = 22000  # translucent window + system backdrops are a Windows 11 feature for this app
BACKDROP_TYPE_BUILD = 22621  # Windows 11 22H2: DWMWA_SYSTEMBACKDROP_TYPE exists from here

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE, DWMSBT_ACRYLIC = 1, 3
WCA_ACCENT_POLICY = 19
ACCENT_DISABLED, ACCENT_ACRYLIC = 0, 4
GWL_STYLE = -16
WS_SYSMENU = 0x00080000
SWP_FRAME_CHANGED = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED
SPI_GETDESKWALLPAPER = 0x0073


def windows_build() -> int:
    return sys.getwindowsversion().build if IS_WINDOWS else 0


def translucent_supported() -> bool:
    """The main window can be translucent (needed for Acrylic) on this system."""
    return IS_WINDOWS and windows_build() >= WIN11_BUILD


def transparency_enabled() -> bool:
    """The Windows "Transparency effects" switch. Missing value means on, which is Windows' default."""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except OSError:
        return True


def acrylic_available() -> bool:
    """The system Acrylic: Windows 11 and Transparency effects is on."""
    return translucent_supported() and transparency_enabled()


# // the native window

if IS_WINDOWS:

    class _AccentPolicy(ctypes.Structure):
        _fields_ = [
            ("AccentState", ctypes.c_int),
            ("AccentFlags", ctypes.c_int),
            ("GradientColor", ctypes.c_uint),  # 0xAABBGGRR
            ("AnimationId", ctypes.c_int),
        ]

    class _CompositionData(ctypes.Structure):
        _fields_ = [
            ("Attribute", ctypes.c_int),
            ("Data", ctypes.c_void_p),
            ("SizeOfData", ctypes.c_size_t),
        ]

    class _Margins(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_int),
            ("right", ctypes.c_int),
            ("top", ctypes.c_int),
            ("bottom", ctypes.c_int),
        ]

    _configured = False

    def _configure() -> None:
        global _configured
        if _configured:
            return
        _configured = True
        user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
        user32.SetWindowCompositionAttribute.argtypes = [wintypes.HWND, ctypes.POINTER(_CompositionData)]
        user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
        user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_Margins)]
        dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long

    def _accent(hwnd: int, state: int) -> bool:
        policy = _AccentPolicy(state, 2, 0, 0)  # the tint is painted by the app, so the system adds none
        data = _CompositionData(
            WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p), ctypes.sizeof(policy)
        )
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))

    def _backdrop_type(hwnd: int, value: int) -> bool:
        number = ctypes.c_int(value)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(number), ctypes.sizeof(number))
        return result == 0

    def _extend_frame(hwnd: int, amount: int) -> None:
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(amount, amount, amount, amount)))

    def _system_menu(hwnd: int, enabled: bool) -> None:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        wanted = style | WS_SYSMENU if enabled else style & ~WS_SYSMENU
        if wanted != style:
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, wanted)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAME_CHANGED)


def apply_system_acrylic(hwnd: int, on: bool) -> bool:
    """Switch the system Acrylic behind the window `hwnd` on or off; True when the window is now as asked.

    The window must already be translucent (WA_TranslucentBackground, set before it was created) and
    must paint its own tint, since the system adds none. Turning it off always succeeds.

    The extended frame makes Windows draw its own minimize / maximize / close buttons over the window, which
    has buttons of its own, so the window loses WS_SYSMENU (the style those buttons need) while the Acrylic is on.
    """
    if not IS_WINDOWS:
        return not on
    try:
        _configure()
        if not on:
            _accent(hwnd, ACCENT_DISABLED)
            _backdrop_type(hwnd, DWMSBT_NONE)
            _extend_frame(hwnd, 0)
            _system_menu(hwnd, True)
            return True
        _extend_frame(hwnd, -1)
        if windows_build() >= BACKDROP_TYPE_BUILD and _backdrop_type(hwnd, DWMSBT_ACRYLIC):
            _system_menu(hwnd, False)
            return True
        if _accent(hwnd, ACCENT_ACRYLIC):  # the older route, for builds without the backdrop attribute
            _system_menu(hwnd, False)
            return True
        _extend_frame(hwnd, 0)
        return False
    except (OSError, AttributeError, ValueError):
        return not on


# // the desktop wallpaper

@dataclass(frozen=True)
class DesktopWallpaper:
    path: str  # "" when the desktop is a plain colour (or the picture is gone)
    style: str  # one of the fit styles of mica_render
    fill: tuple[int, int, int]  # the desktop colour behind / around the picture
    stamp: int  # modification time of the picture, so a changed file counts as a changed wallpaper


def desktop_wallpaper() -> DesktopWallpaper | None:
    """How the desktop shows its wallpaper right now, or None off Windows."""
    if not IS_WINDOWS:
        return None
    import winreg

    path = ""
    try:
        _configure()
        buffer = ctypes.create_unicode_buffer(520)
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, len(buffer), buffer, 0)
        path = buffer.value
    except (OSError, AttributeError):
        pass

    def read(subkey: str, name: str, default: str) -> str:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except OSError:
            return default

    style = style_from_registry(
        read(r"Control Panel\Desktop", "WallpaperStyle", "10"), read(r"Control Panel\Desktop", "TileWallpaper", "0")
    )
    fill = (0, 0, 0)
    try:
        red, green, blue = (int(part) for part in read(r"Control Panel\Colors", "Background", "0 0 0").split()[:3])
        fill = (max(0, min(255, red)), max(0, min(255, green)), max(0, min(255, blue)))
    except ValueError:
        pass
    stamp = 0
    if path:
        try:
            stamp = os.stat(path).st_mtime_ns
        except OSError:
            path = ""
    return DesktopWallpaper(path, style, fill, stamp)