import math
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


def _gear_points() -> str:
    teeth = 8
    points = []
    for index in range(teeth * 4):
        angle = (index - 0.5) * (math.pi * 2 / (teeth * 4))
        radius = 10.0 if index % 4 in (0, 1) else 7.6
        points.append(f"{12 + radius * math.cos(angle):.2f},{12 + radius * math.sin(angle):.2f}")
    return " ".join(points)


_PATHS = {
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.8-.9 1.6-1.9-.2-1 .5-1.8 1.5-1.8H17a4 4 0 0 0 4-4C21 6.9 17 3 12 3Z"/>'
    '<circle cx="7.5" cy="11" r="1"/><circle cx="10.5" cy="7.2" r="1"/><circle cx="15" cy="7.8" r="1"/>',
    "tile-edit": '<path d="M11 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>'
    '<path d="M18.4 3.6a1.9 1.9 0 0 1 2.7 2.7L12 15.4 8 16l.6-4Z"/>',
    "location": '<circle cx="12" cy="7" r="3"/><path d="M12 10v4"/><path d="M6 21l3-7h6l3 7"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "mouse": '<rect x="6" y="3" width="12" height="18" rx="6"/><path d="M12 7v4"/>',
    "image-zoom": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="9" cy="9" r="1.6"/>'
    '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="7.8" r="0.6"/>',
    "broom": '<path d="M14 4l6 6"/><path d="M13 5l-7 7 6 6 7-7"/><path d="M6 12l-3 7 7-3"/>',
    "storage": '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13"/>'
    '<path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-3.8-3.8"/>',
    "gallery": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="9" cy="9" r="2"/>'
    '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z"/>'
    '<circle cx="12" cy="13" r="3.4"/>',
    "video": '<rect x="2" y="6" width="14" height="12" rx="2.5"/><path d="m16 10.5 5.2-3.1a.6.6 0 0 1 .8.5v8.2a.6.6 0 0 1-.8.5L16 13.5"/>',
    "play": '<path d="M7 4.5v15l13-7.5Z"/>',
    "pause": '<rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/>',
    "volume": '<path d="M11 5 6 9H3v6h3l5 4Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/>'
    '<path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "repeat-1": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/>'
    '<path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/>',
    "shuffle": '<path d="m18 14 4 4-4 4"/><path d="m18 2 4 4-4 4"/><path d="M2 18h1.973a4 4 0 0 0 3.3-1.7l5.454-8.6a4 4 0 0 1 3.3-1.7H22"/>'
    '<path d="M2 6h1.972a4 4 0 0 1 3.6 2.2"/><path d="M22 18h-6.041a4 4 0 0 1-3.3-1.8l-.359-.45"/>',
    "list-ordered": '<path d="M10 12h11"/><path d="M10 18h11"/><path d="M10 6h11"/><path d="M4 10h2"/><path d="M4 6h1v4"/>'
    '<path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
    "arrow-right-to-line": '<path d="M17 12H3"/><path d="m11 18 6-6-6-6"/><path d="M21 5v14"/>',
    "volume-off":'<path d="M11 5 6 9H3v6h3l5 4Z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>',
    "album": '<rect x="3" y="8" width="18" height="13" rx="2.5"/><path d="M6 4.5h12M8.5 1.5h7"/>'
    '<circle cx="12" cy="14.5" r="2.3"/>',
    "album-minus": '<rect x="3" y="8" width="18" height="13" rx="2.5"/><path d="M6 4.5h12M8.5 1.5h7"/>'
    '<path d="M9 14.5h6"/>',
    "heart": '<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2'
    'A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7Z"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4'
    'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "folder-plus": '<path d="M10 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20'
    'a2 2 0 0 1 2 2v3"/><circle cx="17.5" cy="17.5" r="4.5"/><path d="M17.5 15.2v4.6M15.2 17.5h4.6"/>',
    "computer": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "image-open": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "edit-image": '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
    "copy-path": '<path d="M9 17H7A5 5 0 0 1 7 7h2M15 7h2a5 5 0 1 1 0 10h-2M8 12h8"/>',
    "folder-search": '<path d="M10.7 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20'
    'a2 2 0 0 1 2 2v4.1"/><path d="m21 21-1.9-1.9"/><circle cx="17" cy="17" r="3"/>',
    "share": '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>'
    '<polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>',
    "import": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
    "settings": f'<polygon points="{_gear_points()}"/><circle cx="12" cy="12" r="3"/>',
    "select": '<rect x="3" y="3" width="18" height="18" rx="3.5"/><path d="m7.8 12.4 2.9 2.9 5.5-6"/>',
    "select-all": '<path d="M7 7V6.5A3.5 3.5 0 0 1 10.5 3h7A3.5 3.5 0 0 1 21 6.5v7a3.5 3.5 0 0 1-3.5 3.5H17"/>'
    '<rect x="3" y="7" width="14" height="14" rx="3.5"/><path d="m6.6 14.4 2.5 2.5 4.4-4.9"/>',
    "file-image": '<path d="M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8Z"/>'
    '<path d="M14 3v3.5A1.5 1.5 0 0 0 15.5 8H19"/><circle cx="10" cy="12.2" r="1.2"/>'
    '<path d="m7.5 19 3.2-3.2 2 2 1.6-1.6L17 18.8"/>',
    "slideshow": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="m10 8 6 4-6 4Z"/>',
    "sort": '<path d="m21 16-4 4-4-4M17 20V4M3 8l4-4 4 4M7 4v16"/>',
    "filter": '<path d="M22 3H2l8 9.5V19l4 2v-8.5Z"/>',
    "layout": '<rect x="3.5" y="3.5" width="9.5" height="7" rx="1.5"/><rect x="16" y="3.5" width="4.5" height="7" rx="1.5"/>'
    '<rect x="3.5" y="13.5" width="4.5" height="7" rx="1.5"/><rect x="11" y="13.5" width="9.5" height="7" rx="1.5"/>',
    "layout-square": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/>'
    '<rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    "size-small": '<rect x="3" y="3" width="18" height="18" rx="3.5"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
    "size-medium": '<rect x="3" y="3" width="18" height="18" rx="3.5"/><path d="M12 3v18M3 12h18"/>',
    "size-large": '<rect x="3" y="3" width="18" height="18" rx="3.5"/>',
    "more": '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    "check": '<path d="m20 6-11 11-5-5"/>',
    "map-pin": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "calendar": '<rect x="3" y="4" width="18" height="17" rx="2.5"/><path d="M3 10h18M8 2v4M16 2v4"/>',
    "text": '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    "size": '<path d="M4 20V4M20 20V4M4 12h16M8 8l-4 4 4 4M16 8l4 4-4 4"/>',
    "arrow-up": '<path d="M12 19V5M5 12l7-7 7 7"/>',
    "arrow-down": '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    "box": '<path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "square": '<rect x="4" y="4" width="16" height="16" rx="2"/>',
    "export": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5M12 3v12"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>',
    "min": '<path d="M5 12h14"/>',
    "max": '<rect x="5" y="5" width="14" height="14" rx="1.5"/>',
    "restore": '<rect x="4.5" y="8.5" width="11" height="11" rx="1.5"/><path d="M8.5 8.5V6.5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-2"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "dot": '<circle cx="12" cy="12" r="4"/>',
    "trash": '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M10 11v6M14 11v6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "plus-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "rename": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4'
    'a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "folder-move": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4'
    'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M9 13h6m-2.5-2.5L15 13l-2.5 2.5"/>',
    "print": '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="9" rx="2"/><path d="M6 14h12v7H6z"/>',
    "wallpaper": '<rect x="3" y="4" width="18" height="12" rx="2"/><circle cx="8.5" cy="8.3" r="1.2"/>'
    '<path d="m3 14 4.4-3.9a1.5 1.5 0 0 1 2 0L14 14.2l1.6-1.4a1.5 1.5 0 0 1 2 0L21 15"/><path d="M8 20h8M12 16v4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "zoom-in": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-3.8-3.8M11 8v6M8 11h6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-3.8-3.8M8 11h6"/>',
    "fit-screen": '<path d="M4 9V6a2 2 0 0 1 2-2h3M15 4h3a2 2 0 0 1 2 2v3M20 15v3a2 2 0 0 1-2 2h-3M9 20H6a2 2 0 0 1-2-2v-3"/>',
    "fullscreen": '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "adjust": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
    'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "filters": '<circle cx="9" cy="9" r="6"/><circle cx="15" cy="15" r="6"/>',
    "markup": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
    "erase": '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/>'
    '<path d="M22 21H7"/><path d="m5 11 9 9"/>',
    "background": '<rect x="3" y="3" width="18" height="18" rx="2.5" stroke-dasharray="2.5 2.5"/><circle cx="12" cy="10" r="2.6"/>'
    '<path d="M6.5 19c.8-3 3-4.4 5.5-4.4s4.7 1.4 5.5 4.4"/>',
    # the Adjustment tool's sliders (brightness uses "adjust")
    "adj-exposure": '<circle cx="12" cy="12" r="9"/><path d="M17.5 6.5l-11 11"/><path d="M8.2 8.2h3M9.7 6.7v3"/>'
    '<path d="M13 15.8h3"/>',
    "adj-contrast": '<circle cx="12" cy="12" r="9"/><path d="M12 3v18"/><path d="M7.5 9v6"/>',
    "adj-highlights": '<circle cx="12" cy="12" r="9" stroke-dasharray="2 3"/><circle cx="12" cy="12" r="3.2"/>',
    "adj-shadows": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18 4.5 9 0 0 1 0-18Z"/>',
    "adj-vignette": '<rect x="3" y="3" width="18" height="18" rx="4.5"/><circle cx="12" cy="12" r="5"/>',
    "adj-saturation": '<path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/>'
    '<path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.4.4a2.1 2.1 0 1 1-3 3l-3.8-3.8a2.1 2.1 0 1 1 3-3l.4.4Z"/>',
    "adj-warmth": '<path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/>',
    "adj-tint": '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
    "adj-sharpness": '<path d="M6 3h12l4 6-10 13L2 9Z"/><path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/>',
    "arrow-left": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
}


def _svg(name: str, color: str, fill: str, width: float) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
        f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>'
    ).encode()


def _device_ratio() -> float:
    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    return max(1.0, screen.devicePixelRatio()) if screen else 1.0


def draw_icon(
    painter: QPainter,
    name: str,
    rect: QRectF,
    color: str | QColor,
    filled: bool = False,
    stroke: float = 1.8,
) -> None:
    color_name = QColor(color).name()
    fill = color_name if filled or name in ("more", "dot") else "none"
    _renderer(name, color_name, fill, stroke).render(painter, rect)


@lru_cache(maxsize=512)
def _renderer(name: str, color: str, fill: str, stroke: float) -> QSvgRenderer:
    """One parsed renderer per distinct icon look.

    Parsing the SVG on every draw call was the real cost of animating the sidebar: each repaint
    drew ~2 icons per row, so every frame re-parsed dozens of SVGs (about 15 fps). A renderer is
    cheap to reuse; only the parse is expensive.
    """
    return QSvgRenderer(QByteArray(_svg(name, color, fill, stroke)))


# // theme-aware icons
# Callers pass the dark theme's literal colors ("#f2f2f2", "#9a9a9a", ...). Those are mapped to
# palette roles so the icon is drawn in whichever theme is active *when it is painted*, which
# means an already-created toolbar icon follows a live theme switch without being rebuilt.
_ROLE_BY_LITERAL = {
    "#f2f2f2": "icon",
    "#ffffff": "icon",
    "#c5c5c5": "icon",
    "#b8b8b8": "icon_muted",
    "#9a9a9a": "icon_muted",
}


def _role_for(color: str | None) -> str | None:
    if color is None:
        return "icon"
    return _ROLE_BY_LITERAL.get(QColor(color).name().lower())


def _role_color(role: str) -> str:
    from ..theme import theme  # local import: theme imports this module

    return getattr(theme.palette(), role)


class _ThemedIconEngine(QIconEngine):
    def __init__(self, name: str, role: str, size: int, filled: bool = False, chevron: bool = False):
        super().__init__()
        self._name, self._role, self._size = name, role, size
        self._filled, self._chevron = filled, chevron

    def clone(self):
        return _ThemedIconEngine(self._name, self._role, self._size, self._filled, self._chevron)

    def _draw(self, painter: QPainter, rect: QRectF, mode) -> None:
        tone = QColor(_role_color(self._role))
        if mode == QIcon.Mode.Disabled:
            tone.setAlphaF(0.4)
        side = min(rect.width(), rect.height())
        if self._chevron:
            chevron, gap = 10.0, 5.0
            glyph = QRectF(rect.left(), rect.top() + (rect.height() - self._size) / 2, self._size, self._size)
            draw_icon(painter, self._name, glyph, tone, self._filled)
            draw_icon(
                painter, "chevron-down",
                QRectF(glyph.right() + gap, rect.top() + (rect.height() - chevron) / 2, chevron, chevron),
                tone, stroke=2.2,
            )
        else:
            glyph = QRectF(rect.left() + (rect.width() - side) / 2, rect.top() + (rect.height() - side) / 2, side, side)
            draw_icon(painter, self._name, glyph, tone, self._filled)

    def paint(self, painter, rect, mode, state) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw(painter, QRectF(rect), mode)
        painter.restore()

    def pixmap(self, size, mode, state) -> QPixmap:
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw(painter, QRectF(0, 0, size.width(), size.height()), mode)
        painter.end()
        return result

    def actualSize(self, size, mode, state):
        return size


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 18, filled: bool = False) -> QPixmap:
    ratio = _device_ratio()
    result = QPixmap(int(size * ratio), int(size * ratio))
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_icon(painter, name, QRectF(0, 0, size, size), color, filled)
    painter.end()
    return result


def icon(name: str, color: str | None = None, size: int = 18, filled: bool = False) -> QIcon:
    role = _role_for(color)
    if role is None:  # an explicit, theme-independent color (e.g. a danger red)
        return QIcon(pixmap(name, color, size, filled))
    return QIcon(_ThemedIconEngine(name, role, size, filled))


def icon_with_chevron(name: str, color: str | None = None, size: int = 18) -> QIcon:
    role = _role_for(color) or "icon"
    return QIcon(_ThemedIconEngine(name, role, size, chevron=True))


def themed_pixmap(name: str, role: str = "icon", size: int = 18, filled: bool = False) -> QPixmap:
    """A pixmap in the active theme's color for `role` (re-request it after a theme change)."""
    return pixmap(name, _role_color(role), size, filled)