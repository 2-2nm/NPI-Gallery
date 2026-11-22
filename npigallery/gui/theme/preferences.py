"""User preferences backed by QSettings, with a change signal so the UI reacts live."""

from PySide6.QtCore import QObject, QSettings, Signal

from ...config import APP_NAME

DEFAULTS: dict[str, object] = {
    "theme": "dark",  # dark | light | system
    "accent_mode": "system",  # system (Windows accent) | automatic (from the sidebar picture) | manual
    "accent_color": "#0078d4",
    "accent_recent": "",
    "accent_custom": "",
    "tile_attributes": True,
    "location_features": False,
    "confirm_delete": True,
    "mouse_wheel": "zoom",  # zoom | navigate
    "zoom_preference": "actual",  # actual | fit
    "auto_hide_controls": True,  # photo viewer: fade the floating controls after a short idle
    "hide_controls_on_zoom": False,  # photo viewer: fade the floating controls while zooming with the wheel
    "video_seek_seconds": "10",  # 5 | 10: how far a double-click on the left / right of a video skips
    "video_loop": False,  # photo viewer: the loop button of the video bar (remembered between sessions)
    "gif_autoplay": "hover",  # gallery grid: never | hover | always (animated GIF thumbnails)
    "video_play_mode": "",  # photo viewer: once | repeat | order | shuffle ("" = not chosen yet: follows the old video_loop)
    "video_volume": 100,  # photo viewer: the volume slider of the video bar, 0-100 (remembered between sessions)
    "video_muted": False,  # photo viewer: the mute toggle of the video bar (remembered between sessions)
    "sidebar_bg_mode": "none",  # none | picture
    "sidebar_bg_path": "",
    "sidebar_bg_crop": "0.5,0.5,1.0",  # centre x, centre y, zoom of the part of the picture that is shown
    "sidebar_bg_dim": 60,  # 0-100: how much of the sidebar color is laid over the picture
    "sidebar_bg_blur": 4,  # 0-24
    "window_bg_mode": "none",  # none | picture | mica | acrylic_manual | acrylic: what is behind everything in the main window
    # (mica = the blurred desktop wallpaper, drawn by the app; acrylic_manual = the look of Windows' Acrylic, also drawn by the app;
    # acrylic = the system blur, only while Windows has Transparency effects on)
    "window_bg_path": "",
    "window_bg_crop": "0.5,0.5,1.0",  # centre x, centre y, zoom of the part of the picture that is shown
    "window_bg_dim": 65,  # 0-100: how much of the window color is laid over the picture / the Mica wallpaper / the Acrylic
    "window_bg_blur": 6,  # 0-24
    "window_mica_blur": 16,  # 0-24: how blurred the Mica wallpaper is
    "window_acrylic_blur": 6,  # 0-24: how blurred the manual Acrylic is
    "float_opacity": 100,  # 30-100: how opaque the floating panels of the main window are (top bar, search, timeline label, menus)
    "video_hwaccel": "auto",  # auto | none | gpu:<vendor>:<device>  (applies after a restart)
}


class Preferences(QObject):
    changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings(APP_NAME, APP_NAME)

    def get(self, key: str):
        default = DEFAULTS[key]
        if isinstance(default, bool):
            return self._settings.value(f"pref/{key}", default, bool)
        if isinstance(default, int):
            return self._settings.value(f"pref/{key}", default, int)
        return self._settings.value(f"pref/{key}", default, str)

    def set(self, key: str, value) -> None:
        if self.get(key) == value:
            return
        self._settings.setValue(f"pref/{key}", value)
        self.changed.emit(key)


_instance: Preferences | None = None


def prefs() -> Preferences:
    global _instance
    if _instance is None:
        _instance = Preferences()
    return _instance