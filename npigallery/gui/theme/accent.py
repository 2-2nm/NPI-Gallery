import colorsys
import math
import os
import sys
from dataclasses import replace

from .preferences import prefs

MODE_SYSTEM = "system"  # the Windows accent colour; what a fresh install uses
MODE_AUTOMATIC = "automatic"  # taken from the sidebar picture, grey while there is none
MODE_MANUAL = "manual"
MODES = (MODE_SYSTEM, MODE_AUTOMATIC, MODE_MANUAL)

APPLY_KEYS = ("accent_mode", "accent_color")
SIDEBAR_KEYS = ("sidebar_bg_mode", "sidebar_bg_path", "sidebar_bg_crop")  # these move the Automatic colour
WATCHED_KEYS = APPLY_KEYS + ("accent_recent", "accent_custom")

MIN_CONTRAST = 2.0
RECENT_LIMIT = 6
CUSTOM_LIMIT = 6
DARK_TEXT = "#141414"
LIGHT_TEXT = "#ffffff"

PRESETS = (
    ("Gold", "#ffb900"),
    ("Orange", "#f7630c"),
    ("Brick red", "#e74856"),
    ("Crimson", "#c50f1f"),
    ("Pink", "#e3008c"),
    ("Orchid", "#b146c2"),
    ("Purple", "#8764b8"),
    ("Indigo", "#6b69d6"),
    ("Blue", "#0078d4"),
    ("Sky", "#4cc2ff"),
    ("Teal", "#00b7c3"),
    ("Sea green", "#00b294"),
    ("Green", "#10893e"),
    ("Lime", "#8cbd18"),
    ("Slate", "#68768a"),
    ("Light gray", "#c8c8c8"),
)


PRESET_VALUES = tuple(value for _, value in PRESETS)


def normalize(value) -> str | None:
    text = str(value or "").strip().lstrip("#").lower()
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdef" for ch in text):
        return None
    return f"#{text}"


def to_hsv(value: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(*_channels(value))


def to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(round(c * 255) for c in _channels(value))


def from_rgb(red: int, green: int, blue: int) -> str:
    return "#" + "".join(f"{max(0, min(255, int(c))):02x}" for c in (red, green, blue))


def from_hsv(hue: float, saturation: float, value: float) -> str:
    return _hex(colorsys.hsv_to_rgb(hue % 1.0 if hue < 1.0 else 1.0, saturation, value))


def _channels(value: str) -> tuple[float, float, float]:
    text = normalize(value)
    return tuple(int(text[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(channels) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in channels)


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(value: str) -> float:
    r, g, b = (_linear(c) for c in _channels(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    a, b = luminance(first), luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def on_color(background: str) -> str:
    if contrast_ratio(background, LIGHT_TEXT) >= contrast_ratio(background, DARK_TEXT):
        return LIGHT_TEXT
    return DARK_TEXT


def mix(first: str, second: str, amount: float) -> str:
    return _hex(a + (b - a) * amount for a, b in zip(_channels(first), _channels(second)))


def with_alpha(value: str, alpha: float) -> str:
    return f"#{round(max(0.0, min(1.0, alpha)) * 255):02x}{normalize(value)[1:]}"


def readable_on(color: str, background: str, dark: bool) -> str:
    result = normalize(color)
    hue, lightness, saturation = colorsys.rgb_to_hls(*_channels(result))
    direction = 1 if dark else -1
    for _ in range(50):
        if contrast_ratio(result, background) >= MIN_CONTRAST:
            break
        lightness = max(0.0, min(1.0, lightness + direction * 0.02))
        result = _hex(colorsys.hls_to_rgb(hue, lightness, saturation))
    return result


def derive(palette, color: str):
    tone = readable_on(color, palette.float_bg, palette.dark)
    on = on_color(tone)
    hover = mix(tone, "#ffffff" if palette.dark else "#000000", 0.14)
    return replace(
        palette,
        accent=tone,
        on_accent=on,
        nav_active_bg=tone,
        nav_active_fg=on,
        selected_tint=with_alpha(tone, 0.18),
        focus_line=tone,
        checked_border=tone,
        default_bg=tone,
        default_fg=on,
        default_hover_bg=hover,
        timeline_active=tone,
        highlight=mix(palette.base, tone, 0.45),
    )


def _windows_accent() -> str | None:
    """The accent chosen in Windows Settings, read from the registry (DWORD, stored as 0xAABBGGRR).

    QStyleHints.accentColor() is not reliable here: it needs Qt 6.6+, can come back invalid, and the app
    installs its own palette, so the registry is asked first.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    sources = (
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "AccentColorMenu"),
        (r"Software\Microsoft\Windows\DWM", "AccentColor"),
    )
    for path, name in sources:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if kind != winreg.REG_DWORD:
            continue
        value = int(value) & 0xFFFFFFFF
        return from_rgb(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)
    return None


def _qt_accent() -> str | None:
    try:
        from PySide6.QtGui import QGuiApplication

        color = QGuiApplication.styleHints().accentColor()
    except (ImportError, AttributeError):
        return None
    return normalize(color.name()) if color.isValid() else None


def system_accent() -> str | None:
    return _windows_accent() or _qt_accent()


def current_mode() -> str:
    """The chosen mode. A value saved by an older version ("default") counts as the Windows accent."""
    value = prefs().get("accent_mode")
    return value if value in MODES else MODE_SYSTEM


GRAY_ACCENT = "#8a8a8a"
HUE_BINS = 24
SAMPLE_SIDE = 48  # the picture is shrunk to this before it is counted
MIN_SATURATION = 0.2  # greyer or darker pixels do not vote for a colour
MIN_VIVID_SHARE = 0.04  # a picture with fewer vivid pixels than this is treated as grey
SIDEBAR_ASPECT = 224 / 860  # the sidebar's usual width / height: which part of the picture is visible

_sidebar_cache: tuple[tuple, str] | None = None


def dominant_color(image) -> str:
    """The most prominent colour of a PIL picture, moved into a range that works as an accent.

    Like Windows does with a wallpaper: every vivid pixel votes for its hue (stronger when more
    saturated), the winning hue is averaged, then saturation and brightness are kept usable. A
    picture without vivid colour gives grey.
    """
    small = image.convert("RGB")
    small.thumbnail((SAMPLE_SIDE, SAMPLE_SIDE))
    pixels = list(small.getdata())
    if not pixels:
        return GRAY_ACCENT
    weight = [0.0] * HUE_BINS
    cos_sum = [0.0] * HUE_BINS
    sin_sum = [0.0] * HUE_BINS
    sat_sum = [0.0] * HUE_BINS
    val_sum = [0.0] * HUE_BINS
    vivid = 0
    for red, green, blue in pixels:
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if saturation < MIN_SATURATION or value < MIN_SATURATION:
            continue
        vivid += 1
        vote = saturation * saturation * value
        index = min(HUE_BINS - 1, int(hue * HUE_BINS))
        weight[index] += vote
        cos_sum[index] += vote * math.cos(hue * math.tau)
        sin_sum[index] += vote * math.sin(hue * math.tau)
        sat_sum[index] += vote * saturation
        val_sum[index] += vote * value
    if vivid / len(pixels) < MIN_VIVID_SHARE:
        return GRAY_ACCENT
    # A hue spread over two neighbouring bins should win against a single sharper one.
    scores = [weight[i] + 0.5 * (weight[i - 1] + weight[(i + 1) % HUE_BINS]) for i in range(HUE_BINS)]
    best = max(range(HUE_BINS), key=scores.__getitem__)
    if weight[best] <= 0:
        return GRAY_ACCENT
    hue = (math.atan2(sin_sum[best], cos_sum[best]) / math.tau) % 1.0
    saturation = max(0.45, min(0.9, sat_sum[best] / weight[best]))
    value = max(0.6, min(0.95, val_sum[best] / weight[best]))
    return from_hsv(hue, saturation, value)


def sidebar_accent() -> str:
    """Accent for Automatic mode: the dominant colour of the part of the sidebar picture that is shown.

    Grey when no picture is set (or it can't be read). Cached, because the viewer asks on every paint.
    """
    global _sidebar_cache
    store = prefs()
    path = str(store.get("sidebar_bg_path") or "")
    if store.get("sidebar_bg_mode") != "picture" or not path:
        return GRAY_ACCENT
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return GRAY_ACCENT
    crop_text = str(store.get("sidebar_bg_crop"))
    key = (path, stamp, crop_text)
    if _sidebar_cache is not None and _sidebar_cache[0] == key:
        return _sidebar_cache[1]
    from ..sidebar import background

    source = background.source_image(path)
    if source is None:
        return GRAY_ACCENT
    cx, cy, zoom = background.parse_crop(crop_text)
    x, y, width, height = background.crop_region(source.width, source.height, SIDEBAR_ASPECT, cx, cy, zoom)
    region = source.crop((round(x), round(y), max(round(x) + 1, round(x + width)), max(round(y) + 1, round(y + height))))
    color = dominant_color(region)
    _sidebar_cache = (key, color)
    return color


def selection() -> str | None:
    mode = current_mode()
    if mode == MODE_MANUAL:
        return normalize(prefs().get("accent_color"))
    if mode == MODE_AUTOMATIC:
        return sidebar_accent()
    return system_accent()


def apply(palette):
    chosen = selection()
    return palette if chosen is None else derive(palette, chosen)


def pair(background: str, dark: bool) -> tuple[str, str] | None:
    chosen = selection()
    if chosen is None:
        return None
    tone = readable_on(chosen, background, dark)
    return tone, on_color(tone)


def recent() -> list[str]:
    items = [normalize(item) for item in str(prefs().get("accent_recent")).split()]
    return [item for item in items if item][:RECENT_LIMIT]


def custom() -> list[str]:
    items = [normalize(item) for item in str(prefs().get("accent_custom")).split()]
    return [item for item in items if item][:CUSTOM_LIMIT]


def choose(color: str) -> None:
    value = normalize(color)
    if value is None:
        return
    store = prefs()
    history = [value] + [item for item in recent() if item != value]
    store.set("accent_recent", " ".join(history[:RECENT_LIMIT]))
    if value not in PRESET_VALUES:
        saved = [value] + [item for item in custom() if item != value]
        store.set("accent_custom", " ".join(saved[:CUSTOM_LIMIT]))
    store.set("accent_color", value)
    store.set("accent_mode", MODE_MANUAL)