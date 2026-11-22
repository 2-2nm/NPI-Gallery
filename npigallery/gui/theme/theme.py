from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from ...config import cache_directory
from . import accent
from .preferences import prefs
from ..common.icons import pixmap


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    chrome: str
    content: str
    content_edge: str
    control: str
    control_hover: str
    control_pressed: str
    stroke: str
    stroke_hover: str
    text: str
    text_dim: str
    text_muted: str
    disabled_text: str
    disabled_bg: str
    base: str
    alt_base: str
    tooltip_bg: str
    highlight: str
    menu_bg: str
    menu_border: str
    menu_hover: str
    scroll: str
    scroll_hover: str
    focus_line: str
    default_bg: str
    default_fg: str
    default_hover_bg: str
    default_disabled_bg: str
    default_disabled_fg: str
    checked_bg: str
    checked_border: str
    search_bg: str
    search_border: str
    search_border_bottom: str
    search_hover_bg: str
    search_hover_border: str
    search_focus_bg: str
    search_focus_border: str
    caption_hover: str
    card: str
    card_stroke: str
    card_hover: str
    switch_off_border: str
    accent: str
    on_accent: str
    icon: str
    icon_muted: str
    placeholder: str
    selected_tint: str
    sidebar_selected: str
    sidebar_hover: str
    separator: str
    timeline_tick: str
    timeline_year: str
    timeline_active: str
    timeline_thumb: str
    pill_bg: str
    pill_border: str
    pill_text: str
    splitter_idle: str
    splitter_drag: str
    danger: str
    thumb_bg: str
    float_bg: str
    float_edge: str
    nav_active_bg: str
    nav_active_fg: str
    footer_button: str
    timeline_bg: str
    heart: str


DARK = Palette(
    name="dark", dark=True,
    chrome="#0b0b0b", content="#0b0b0b", content_edge="#0b0b0b",
    control="#2d2d2d", control_hover="#353535", control_pressed="#262626",
    stroke="#3a3a3a", stroke_hover="#464646",
    text="#ffffff", text_dim="#c5c5c5", text_muted="#9a9a9a",
    disabled_text="#6f6f6f", disabled_bg="#272727",
    base="#1c1c1c", alt_base="#242424", tooltip_bg="#2b2b2b", highlight="#454545",
    menu_bg="#2c2c2c", menu_border="#3d3d3d", menu_hover="#3b3b3b",
    scroll="#6b6b6b", scroll_hover="#8c8c8c", focus_line="#ffffff",
    default_bg="#e8e8e8", default_fg="#141414", default_hover_bg="#ffffff",
    default_disabled_bg="#383838", default_disabled_fg="#7d7d7d",
    checked_bg="#383838", checked_border="#d8d8d8",
    search_bg="#2d2d2d", search_border="#353535", search_border_bottom="#484848",
    search_hover_bg="#313131", search_hover_border="#424242",
    search_focus_bg="#2d2d2d", search_focus_border="#454545",
    caption_hover="#2e2e2e",
    card="#171717", card_stroke="#2a2a2a", card_hover="#1e1e1e", switch_off_border="#d0d0d0",
    accent="#ffffff", on_accent="#141414", icon="#f2f2f2", icon_muted="#9a9a9a",
    placeholder="#1c1c1c", selected_tint="#1cffffff",
    sidebar_selected="#2d2d2d", sidebar_hover="#242424", separator="#2a2a2a",
    timeline_tick="#9d9d9d", timeline_year="#ffffff", timeline_active="#ffffff", timeline_thumb="#d8d8d8",
    pill_bg="#e61c1c1c", pill_border="#3a3a3a", pill_text="#ffffff",
    splitter_idle="#4d4d4d", splitter_drag="#888888",
    danger="#ff6b7a", thumb_bg="#1c1c1c",
    float_bg="#171717", float_edge="#2a2a2a", nav_active_bg="#7aa8ff", nav_active_fg="#0b1220",
    footer_button="#262626", timeline_bg="#e6141414", heart="#ff5d7a",
)

LIGHT = Palette(
    name="light", dark=False,
    chrome="#f1f1f1", content="#f1f1f1", content_edge="#f1f1f1",
    control="#ffffff", control_hover="#f6f6f6", control_pressed="#ececec",
    stroke="#e0e0e0", stroke_hover="#cfcfcf",
    text="#1a1a1a", text_dim="#4a4a4a", text_muted="#6b6b6b",
    disabled_text="#a0a0a0", disabled_bg="#f0f0f0",
    base="#ffffff", alt_base="#f6f6f6", tooltip_bg="#ffffff", highlight="#d9d9d9",
    menu_bg="#fcfcfc", menu_border="#dcdcdc", menu_hover="#ececec",
    scroll="#a8a8a8", scroll_hover="#808080", focus_line="#1a1a1a",
    default_bg="#202020", default_fg="#ffffff", default_hover_bg="#000000",
    default_disabled_bg="#d6d6d6", default_disabled_fg="#8a8a8a",
    checked_bg="#e4e4e4", checked_border="#3a3a3a",
    search_bg="#ffffff", search_border="#e0e0e0", search_border_bottom="#8a8a8a",
    search_hover_bg="#fafafa", search_hover_border="#cfcfcf",
    search_focus_bg="#ffffff", search_focus_border="#cfcfcf",
    caption_hover="#e6e6e6",
    card="#ffffff", card_stroke="#e5e5e5", card_hover="#f7f7f7", switch_off_border="#6b6b6b",
    accent="#1a1a1a", on_accent="#ffffff", icon="#1a1a1a", icon_muted="#6b6b6b",
    placeholder="#e2e2e2", selected_tint="#1c000000",
    sidebar_selected="#e6e6e6", sidebar_hover="#ececec", separator="#dcdcdc",
    timeline_tick="#9a9a9a", timeline_year="#2a2a2a", timeline_active="#1a1a1a", timeline_thumb="#4a4a4a",
    pill_bg="#f2ffffff", pill_border="#d0d0d0", pill_text="#1a1a1a",
    splitter_idle="#c4c4c4", splitter_drag="#8a8a8a",
    danger="#c42b1c", thumb_bg="#ececec",
    float_bg="#ffffff", float_edge="#dedede", nav_active_bg="#2f6fed", nav_active_fg="#ffffff",
    footer_button="#ebebeb", timeline_bg="#f0ffffff", heart="#e5384f",
)

THEME_MODES = ("light", "dark", "system")

_active: Palette = DARK
_mode: str = "dark"


class _ThemeSignals(QObject):
    changed = Signal()


_signals: _ThemeSignals | None = None


def signals() -> _ThemeSignals:
    global _signals
    if _signals is None:
        _signals = _ThemeSignals()
    return _signals


def palette() -> Palette:
    return _active


def mode() -> str:
    return _mode


def color(value: str) -> QColor:
    return QColor(value)


MIN_FLOAT_OPACITY = 30  # below this the text on a floating panel stops being readable over a picture


def float_opacity() -> float:
    """How opaque the floating panels of the main window are, 0.3-1.0 (the "float_opacity" preference)."""
    return max(MIN_FLOAT_OPACITY, min(100, int(prefs().get("float_opacity")))) / 100


def float_color(role: str) -> QColor:
    """A palette color for a floating panel, with the user's opacity applied on top of its own alpha."""
    result = QColor(getattr(_active, role))
    result.setAlphaF(result.alphaF() * float_opacity())
    return result


def _float_rgba(value: str, opacity: float) -> str:
    result = QColor(value)
    return f"rgba({result.red()}, {result.green()}, {result.blue()}, {round(result.alphaF() * opacity * 255)})"


def resolve(theme_mode: str) -> Palette:
    if theme_mode == "light":
        return LIGHT
    if theme_mode == "system":
        hints = QGuiApplication.styleHints()
        try:
            return LIGHT if hints.colorScheme() == Qt.ColorScheme.Light else DARK
        except AttributeError:
            return DARK
    return DARK


FONT_FAMILIES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Helvetica Neue", "Sans Serif"]
FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"


def _load_bundled_fonts() -> None:
    """Register the bundled Inter files so the font looks the same on every machine."""
    for path in sorted(FONT_DIR.glob("*.otf")):
        QFontDatabase.addApplicationFont(str(path))




def _asset(name: str, icon_name: str, tone: str) -> str:
    path: Path = cache_directory() / f"{_active.name}-{name}"
    pixmap(icon_name, tone, 16).save(str(path), "PNG")
    return path.as_posix()


def stylesheet() -> str:
    p = _active
    accent_hover = QColor(p.nav_active_bg).lighter(110).name()
    accent_pressed = QColor(p.nav_active_bg).darker(110).name()
    danger_fg = "#1a0a0c" if p.dark else "#ffffff"
    danger_hover = QColor(p.danger).lighter(108).name()
    danger_pressed = QColor(p.danger).darker(110).name()
    opacity = float_opacity()
    float_bg = _float_rgba(p.float_bg, opacity)
    search_hover_bg = _float_rgba(p.search_hover_bg, opacity)
    menu_bg = _float_rgba(p.menu_bg, opacity)
    return f"""
QMainWindow {{
    background: {p.chrome};
}}
QToolTip {{ background: {p.tooltip_bg}; color: {p.text}; border: 1px solid {p.stroke}; padding: 4px 8px; border-radius: 4px; }}

QLineEdit, QPlainTextEdit {{
    background: {p.control}; color: {p.text}; border: 1px solid {p.stroke};
    border-radius: 5px; padding: 5px 9px; selection-background-color: {p.highlight};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-bottom: 2px solid {p.focus_line}; }}

QPushButton {{
    background: {p.control}; color: {p.text}; border: 1px solid {p.stroke};
    border-radius: 5px; padding: 5px 14px;
}}
QPushButton:hover {{ background: {p.control_hover}; border-color: {p.stroke_hover}; }}
QPushButton:pressed {{ background: {p.control_pressed}; color: {p.text_dim}; }}
QPushButton:disabled {{ color: {p.disabled_text}; background: {p.disabled_bg}; }}
QPushButton:default {{ background: {p.default_bg}; color: {p.default_fg}; border-color: {p.default_bg}; font-weight: 600; }}
QPushButton:default:hover {{ background: {p.default_hover_bg}; border-color: {p.default_hover_bg}; }}
QPushButton:default:disabled {{ background: {p.default_disabled_bg}; color: {p.default_disabled_fg}; border-color: {p.default_disabled_bg}; }}

QToolButton#Tool, QPushButton#Tool {{
    background: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 5px 8px; color: {p.text};
}}
QToolButton#Tool:hover, QPushButton#Tool:hover {{ background: {p.control}; }}
QToolButton#Tool:pressed, QPushButton#Tool:pressed {{ background: {p.control_pressed}; }}
QToolButton#Tool:checked, QPushButton#Tool:checked {{ background: {p.control_hover}; border-color: {p.stroke}; }}
QToolButton#Tool::menu-indicator {{ image: none; width: 0px; }}
QPushButton#Bordered {{
    background: {p.control}; border: 1px solid {p.stroke}; border-radius: 5px; padding: 5px 13px;
}}
QPushButton#Bordered:hover {{ background: {p.control_hover}; border-color: {p.stroke_hover}; }}
QPushButton#Bordered:checked {{ background: {p.checked_bg}; border-color: {p.checked_border}; }}

QMenu {{
    background: {p.menu_bg}; color: {p.text}; border: 1px solid {p.menu_border}; border-radius: 8px; padding: 4px;
}}
/* Menus opened from the main window follow the floating opacity, and so does any menu that sets the
   "floatMenu" property (the photo viewer's, whose window is not a descendant of the main window). */
QMainWindow QMenu, QMenu[floatMenu="true"] {{ background: {menu_bg}; }}
/* Row grid shared by every item, Delete included: icon 16px from the row edge, label ~44px,
   shortcut / chevron 16px from the right edge (left and right breathing room are equal). */
QMenu::item {{ padding: 7px 14px 7px 10px; border-radius: 4px; margin: 1px 2px; }}
QMenu::item:selected {{ background: {p.menu_hover}; }}
QMenu::item:disabled {{ color: {p.disabled_text}; }}
/* the destructive row (add_danger_action) is the menu's default action; same grid, red text */
QMenu::item:default {{ color: {p.danger}; font-weight: normal; }}
QMenu::icon {{ padding-left: 14px; }}
QMenu::separator {{ height: 1px; background: {p.menu_border}; margin: 4px 2px; }}
QMenu::indicator {{ width: 0px; height: 0px; image: none; }}
QMenu::right-arrow {{ image: url({_asset("menu-chevron-right.png", "chevron-right", p.text_dim)}); width: 12px; height: 12px; right: 14px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.scroll}; min-height: 40px; border-radius: 3px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {p.scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p.scroll}; min-width: 40px; border-radius: 3px; margin: 2px 0; }}

QTreeWidget {{ background: {p.content}; alternate-background-color: {p.control}; border: none; }}
QToolButton#Caption, QToolButton#CaptionClose {{ background: transparent; border: none; border-radius: 16px; }}
QToolButton#Caption:hover {{ background: {p.caption_hover}; }}
QToolButton#CaptionClose:hover {{ background: #c42b1c; }}
QWidget#Root, QWidget#TopBar, QWidget#Sidebar, QWidget#Content {{ background: transparent; }}
QFrame#SidebarCard {{ background: {p.float_bg}; border: 1px solid {p.float_edge}; border-radius: 22px; }}
/* The sidebar follows the floating opacity only while it shows no picture of its own (Sidebar sets the property). */
QFrame#SidebarCard[floatTint="true"] {{ background: {float_bg}; }}
QFrame#Pill {{ background: {float_bg}; border: 1px solid {p.float_edge}; border-radius: 23px; }}
QFrame#PillIconHolder {{ background: {p.control}; border: none; border-radius: 18px; }}
QFrame#PillDivider {{ background: {p.float_edge}; border: none; }}
QFrame#Pill QToolButton#Tool {{ border-radius: 16px; padding: 0px 9px; min-height: 32px; }}
QFrame#Pill QToolButton#Tool:hover {{ background: {p.control_hover}; }}
QFrame#Pill QPushButton#Bordered {{
    background: transparent; border: 1px solid transparent; border-radius: 16px;
    padding: 0px 12px; min-height: 32px; font-weight: 600;
}}
QFrame#Pill QPushButton#Bordered:hover {{ background: {p.control_hover}; }}
QFrame#Pill QPushButton#Bordered:checked {{ background: {p.control_hover}; border-color: transparent; }}
QLabel#PillTitle {{ font-size: 11pt; font-weight: 700; color: {p.text}; background: transparent; }}
QLabel#PillSubtitle {{ font-size: 8.5pt; color: {p.text_muted}; background: transparent; }}
QLabel#SidebarTitle {{ font-size: 11.5pt; font-weight: 700; color: {p.text}; background: transparent; }}
QLabel#SidebarLogo {{ background: transparent; border: none; }}
QToolButton#SidebarMenu {{ background: transparent; border: none; border-radius: 8px; }}
QToolButton#SidebarMenu:hover {{ background: {p.control_hover}; }}
QToolButton#SidebarMenu:pressed {{ background: {p.control_pressed}; }}
QLabel#Title {{ font-size: 16pt; font-weight: 600; color: {p.text}; }}
QLabel#Subtitle {{ color: {p.text_dim}; font-size: 9.5pt; }}
QLineEdit#Search {{
    background: {float_bg};
    border: 1px solid {p.float_edge};
    border-radius: 20px;
    padding: 0px 12px 0px 6px;
}}
QLineEdit#Search:hover {{ background: {search_hover_bg}; border-color: {p.stroke_hover}; }}
QLineEdit#Search:focus {{ background: {float_bg}; border: 1px solid {p.text_muted}; }}

QWidget#SettingsBody, QScrollArea#SettingsScroll {{ background: transparent; border: none; }}
QScrollArea#SettingsScroll > QWidget > QWidget {{ background: transparent; }}
QLabel#SettingsPageTitle {{ font-size: 20pt; font-weight: 600; color: {p.text}; }}
QLabel#SectionTitle {{ font-size: 10.5pt; font-weight: 600; color: {p.text}; }}
QFrame#SettingCard {{ background: {p.card}; border: 1px solid {p.card_stroke}; border-radius: 4px; }}
QFrame#SettingCard:hover {{ background: {p.card_hover}; }}
QFrame#SettingDivider {{ background: {p.stroke}; border: none; }}
QSlider#SettingSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {p.stroke_hover}; }}
QSlider#SettingSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider#SettingSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {p.accent}; }}
QSlider#SettingSlider::handle:horizontal:hover {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QLabel#CardTitle {{ color: {p.text}; font-size: 10pt; background: transparent; border: none; }}
QLabel#CardDesc {{ color: {p.text_dim}; font-size: 8.5pt; background: transparent; border: none; }}
QLabel#CardValue {{ color: {p.text}; font-size: 9.5pt; background: transparent; border: none; }}
QLabel#CardState {{ color: {p.text}; font-size: 9.5pt; background: transparent; border: none; }}
QPushButton#Dropdown {{
    background: {p.control}; border: 1px solid {p.stroke}; border-radius: 4px;
    padding: 4px 30px 4px 12px; text-align: left; min-height: 20px;
}}
QPushButton#Dropdown:hover {{ background: {p.control_hover}; border-color: {p.stroke_hover}; }}
QPushButton#Dropdown:pressed {{ background: {p.control_pressed}; }}
QPushButton#Dropdown[open="true"] {{ background: {p.control_pressed}; }}

QFrame#DialogCard {{ background: {p.float_bg}; border: 1px solid {p.float_edge}; border-radius: 22px; }}
QFrame#DialogPanel {{ background: {p.card_hover}; border: 1px solid {p.float_edge}; border-radius: 14px; }}
QLabel#DialogTitle {{ font-size: 11.5pt; font-weight: 700; color: {p.text}; background: transparent; }}
QLabel#DialogLabel {{ color: {p.text_muted}; background: transparent; }}
QLabel#DialogText {{ color: {p.text_dim}; background: transparent; }}
QLabel#DialogName {{ font-size: 10.5pt; font-weight: 600; color: {p.text}; background: transparent; }}
QLabel#DialogDetail {{ font-size: 9pt; color: {p.text_muted}; background: transparent; }}
QLabel#DialogDanger {{ color: {p.danger}; background: transparent; }}
QToolButton#DialogClose {{ background: transparent; border: none; border-radius: 16px; }}
QToolButton#DialogClose:hover {{ background: {p.control_hover}; }}
QToolButton#DialogClose:pressed {{ background: {p.control_pressed}; }}
QLineEdit#DialogInput {{
    background: {p.footer_button}; color: {p.text}; border: 1px solid {p.stroke}; border-radius: 14px;
    padding: 0px 12px; min-height: 34px; selection-background-color: {p.highlight};
}}
QLineEdit#DialogInput:hover {{ border: 1px solid {p.stroke_hover}; }}
QLineEdit#DialogInput:focus {{ background: {p.footer_button}; border: 1px solid {p.text_muted}; }}
QLineEdit#DialogInput:disabled {{ color: {p.disabled_text}; background: {p.disabled_bg}; border: 1px solid {p.stroke}; }}
QPushButton#DialogButton {{
    background: {p.footer_button}; color: {p.text}; border: 1px solid {p.stroke}; border-radius: 14px;
    padding: 0px 20px; min-height: 34px; font-weight: 500;
}}
QPushButton#DialogButton:hover {{ background: {p.control_hover}; border: 1px solid {p.stroke_hover}; }}
QPushButton#DialogButton:pressed {{ background: {p.control_pressed}; color: {p.text_dim}; }}
QPushButton#DialogButton:disabled {{ background: {p.disabled_bg}; color: {p.disabled_text}; border: 1px solid {p.stroke}; }}
QPushButton#DialogPrimary {{
    background: {p.nav_active_bg}; color: {p.nav_active_fg}; border: 1px solid {p.nav_active_bg}; border-radius: 14px;
    padding: 0px 20px; min-height: 34px; font-weight: 600;
}}
QPushButton#DialogPrimary:hover {{ background: {accent_hover}; border: 1px solid {accent_hover}; }}
QPushButton#DialogPrimary:pressed {{ background: {accent_pressed}; border: 1px solid {accent_pressed}; }}
QPushButton#DialogPrimary:disabled {{ background: {p.default_disabled_bg}; color: {p.default_disabled_fg}; border: 1px solid {p.default_disabled_bg}; }}
QPushButton#DialogDanger {{
    background: {p.danger}; color: {danger_fg}; border: 1px solid {p.danger}; border-radius: 14px;
    padding: 0px 20px; min-height: 34px; font-weight: 600;
}}
QPushButton#DialogDanger:hover {{ background: {danger_hover}; border: 1px solid {danger_hover}; }}
QPushButton#DialogDanger:pressed {{ background: {danger_pressed}; border: 1px solid {danger_pressed}; }}
QPushButton#DialogAction {{
    background: transparent; color: {p.text}; border: 1px solid {p.stroke}; border-radius: 14px;
    padding: 0px 16px; min-height: 40px; text-align: left; font-weight: 500;
}}
QPushButton#DialogAction:hover {{ background: {p.footer_button}; border: 1px solid {p.stroke_hover}; }}
QPushButton#DialogAction:pressed {{ background: {p.control_pressed}; }}
QProgressBar#DialogProgress {{
    background: {p.stroke}; border: none; border-radius: 2px; min-height: 4px; max-height: 4px; text-align: center;
}}
QProgressBar#DialogProgress::chunk {{ background: {p.nav_active_bg}; border-radius: 2px; }}
QScrollArea#DialogScroll {{ background: transparent; border: none; }}
QScrollArea#DialogScroll > QWidget > QWidget {{ background: transparent; }}
"""


def _apply_palette(app: QApplication) -> None:
    p = _active
    palette_obj = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.chrome,
        QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.base,
        QPalette.ColorRole.AlternateBase: p.alt_base,
        QPalette.ColorRole.ToolTipBase: p.tooltip_bg,
        QPalette.ColorRole.ToolTipText: p.text,
        QPalette.ColorRole.Text: p.text,
        QPalette.ColorRole.Button: p.control,
        QPalette.ColorRole.ButtonText: p.text,
        QPalette.ColorRole.BrightText: "#ff5555",
        QPalette.ColorRole.Link: p.text_dim,
        QPalette.ColorRole.Highlight: p.highlight,
        QPalette.ColorRole.HighlightedText: p.text,
        QPalette.ColorRole.PlaceholderText: p.text_muted,
    }
    for role, value in roles.items():
        palette_obj.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette_obj.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.disabled_text))
    app.setPalette(palette_obj)


_fonts_ready = False


def apply_theme(app: QApplication, theme_mode: str | None = None) -> None:
    """Install the palette + stylesheet for `theme_mode` (light, dark or system)."""
    global _active, _mode, _fonts_ready
    if theme_mode is not None:
        _mode = theme_mode if theme_mode in THEME_MODES else "dark"
    _active = accent.apply(resolve(_mode))
    app.setStyle("Fusion")
    _apply_palette(app)
    if not _fonts_ready:
        _load_bundled_fonts()
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPointSizeF(9.5)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        app.setFont(font)
        _fonts_ready = True
    app.setStyleSheet(stylesheet())
    signals().changed.emit()


def refresh_stylesheet(app: QApplication) -> None:
    """Re-read the stylesheet only (cheaper than apply_theme); used when just the floating opacity changed."""
    app.setStyleSheet(stylesheet())
    signals().changed.emit()


# Backwards-compatible name used by app.py.
apply_dark_theme = apply_theme