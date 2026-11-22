"""The Settings page: Windows 11 Photos-style cards, switches and dropdowns."""

import platform
import sys
import uuid
from importlib import metadata
from pathlib import Path

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QEvent, QPoint, QProcess, QRect, QRectF, QSize, Qt, QTimer, Signal, qVersion
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...config import APP_TITLE
from ...formatting import human_size
from ...services import shell_registration
from ..theme import accent, theme
from .accent_picker import ColorDot, open_accent_dialog
from .nav import NavList
from .slider import SmoothSlider
from ..common.branding import logo_pixmap
from ..common import gpuinfo, hwdecode
from ..common.dialogs import FloatDialog, button_row, dialog_label, make_button, warn
from ..common.icons import draw_icon, icon
from ..common.motion import (
    DROPDOWN_DURATION,
    DROPDOWN_SLIDE,
    AnimatedMenu,
    FrameAnimation,
    SmoothScrollArea,
    decelerate,
    make_animation,
)
from ..theme.preferences import prefs
from ..sidebar.background import DEFAULT_CROP, IMAGE_FILTER, MAX_BLUR, format_crop, parse_crop, source_pixmap
from ..sidebar.crop import SidebarCropDialog
from ..backdrop import backdrop
from ..backdrop.crop_dialog import BackdropCropDialog
from ..backdrop.mica_render import MAX_MICA_BLUR

COLUMN_MAX_WIDTH = 956
SECTION_GAP = 72  # px between two categories in the one long column
NAV_WIDTH = 240
# (key, label, icon) in the order they appear in the list on the left
CATEGORIES = [
    ("personalization", "Personalization", "palette"),
    ("general", "General", "settings"),
    ("photos", "Photos", "gallery"),
    ("video", "Videos", "video"),
    ("files", "Files", "folder"),
    ("storage", "Storage", "storage"),
    ("about", "About", "info"),
]
CARD_RADIUS = 4
DUPLICATES_TEXT = "Find photos that are exact copies of each other, so the extra copies can be removed to free up space"
IGNORED_TEXT = "Groups and photos you mark as not a duplicate are remembered here"
FADE_HEIGHT = 500  # px of dimming at the top and bottom edge of a page; the content scrolls through it
DETAIL_INDENT = 58

THEME_OPTIONS = [("Light", "light"), ("Dark", "dark"), ("Windows default", "system")]
ACCENT_OPTIONS = [("Windows accent", accent.MODE_SYSTEM), ("Automatic", accent.MODE_AUTOMATIC), ("Manual", accent.MODE_MANUAL)]
WHEEL_OPTIONS = [("Zoom in or out", "zoom"), ("View next or previous items", "navigate")]
ZOOM_OPTIONS = [("Zoom to fit in window", "fit"), ("View at actual size", "actual")]
SEEK_OPTIONS = [("5 seconds", "5"), ("10 seconds", "10")]
GIF_OPTIONS = [("Never", "never"), ("When pointing at it", "hover"), ("Always", "always")]


def hwaccel_options() -> list[tuple[str, str]]:
    """None / Automatic, then every GPU found (both, with their kind, on a hybrid system)."""
    gpus = gpuinfo.detect()
    several = len(gpus) > 1
    options = [("None (CPU)", hwdecode.NONE), ("Automatic", hwdecode.AUTO)]
    options += [(gpuinfo.display_name(gpu, several), gpu.key) for gpu in gpus]
    return options


# What the app is, one card each: (icon, title, text)
ABOUT_APP = [
    (
        "gallery", "A gallery for your own photos and videos",
        "NPI Gallery shows the pictures and clips that are already in your folders, grouped by day with a "
        "year timeline, and opens them in an immersive viewer. It is a desktop program written in Python on top of Qt.",
    ),
    (
        "folder", "Your library is your folders",
        "Add folders with Import; their subfolders are always included. Nothing is copied into the app: "
        "files stay where they are and are only changed when you rename, rotate, move or delete them yourself.",
    ),
    (
        "export", "The .npi format",
        "Dump folder converts the images of a folder to .npi files and Restore folder turns them back into regular "
        "images. The same two actions are available on the command line (python -m npigallery dump / restore / verify / info).",
    ),
    (
        "eye", "Photo and video viewer",
        "The viewer has floating controls that fade out when the mouse is idle, a thumbnail filmstrip, zoom, "
        "an info card with EXIF data, a slideshow and printing. Videos play in the same window; "
        "Settings > Videos chooses how they are decoded.",
    ),
    (
        "storage", "What is stored on this computer",
        "Your preferences, the list of folders, favorites and the window layout are kept by Qt in the user settings. "
        "Thumbnails and the media index are two SQLite databases in the cache folder (%LOCALAPPDATA%\\NpiGallery on Windows); "
        "Settings > Storage deletes the thumbnails, and they are made again when needed.",
    ),
    (
        "info", "Privacy",
        "Everything happens on this computer. The program contains no code that connects to the internet, "
        "and location data in photos is only shown in the info panel when you turn that on in Settings > General.",
    ),
]


def _dist_version(*names: str) -> str:
    for name in names:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return ""


# Software that ships with, or runs under, the app: (name, version, license, what it is used for, website)
def third_party() -> list[tuple[str, str, str, str, str]]:
    return [
        (
            "Python", platform.python_version(), "PSF License",
            "The language and runtime. Its standard library provides the SQLite databases, hashing, threads and "
            "the Windows registry and ctypes bridges. SQLite itself is in the public domain.",
            "python.org",
        ),
        (
            "Qt for Python (PySide6)", _dist_version("PySide6") or PYSIDE_VERSION, "LGPL v3",
            f"The Qt {qVersion()} toolkit: every window, control and animation, painting, settings storage, "
            "SVG icons (Qt SVG) and video playback (Qt Multimedia).",
            "doc.qt.io/qtforpython",
        ),
        (
            "FFmpeg (inside Qt Multimedia)", "", "LGPL v2.1 or later",
            "Decodes the video and audio that the viewer plays, on the graphics processor when hardware acceleration is on.",
            "ffmpeg.org",
        ),
        (
            "Pillow", _dist_version("Pillow", "pillow"), "HPND License",
            "Opens and converts images, makes thumbnails and rotates photos.",
            "python-pillow.org",
        ),
        (
            "pillow-heif", _dist_version("pillow-heif", "pillow_heif"), "BSD 3-Clause",
            "HEIC / HEIF photo support for Pillow. It carries libheif (LGPL v3) and its codec libraries, "
            "each under its own license.",
            "github.com/bigcat88/pillow_heif",
        ),
        (
            "Send2Trash", _dist_version("Send2Trash", "send2trash"), "BSD License",
            "Moves deleted photos to the Recycle Bin instead of removing them for good.",
            "github.com/arsenetar/send2trash",
        ),
        (
            "Inter typeface", "", "SIL Open Font License 1.1",
            "The font of the whole interface (Regular, Medium, SemiBold and Bold). \u00a9 The Inter Project Authors.",
            "github.com/rsms/inter",
        ),
        (
            "Windows system interfaces", "", "Part of Windows",
            "Called through ctypes and winreg, not installed with the app: the window frame and title-bar color (DWM), "
            "the desktop wallpaper, the graphics adapter list (DXGI) and the per-app graphics preference.",
            "learn.microsoft.com",
        ),
    ]


def _tone(role: str) -> QColor:
    return QColor(getattr(theme.palette(), role))


class Glyph(QWidget):
    """An icon drawn with the active theme's color at paint time."""

    def __init__(self, name: str, size: int = 20, role: str = "icon", parent=None):
        super().__init__(parent)
        self._name, self._size, self._role = name, size, role
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_icon(painter, self._name, QRectF(0, 0, self._size, self._size), _tone(self._role), stroke=1.6)


class ToggleSwitch(QAbstractButton):
    """Windows-style On/Off switch."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 20)
        self._position = 1.0 if checked else 0.0
        self._animation = make_animation(self, 160, decelerate())
        self._animation.valueChanged.connect(self._on_step)
        self.toggled.connect(self._on_toggled)

    def sizeHint(self) -> QSize:
        return QSize(40, 20)

    def _on_toggled(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _on_step(self, value) -> None:
        self._position = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = rect.height() / 2
        t = self._position
        painter.setPen(Qt.PenStyle.NoPen)
        if t > 0.0:
            fill = _tone("accent")
            fill.setAlphaF(min(1.0, t))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, radius, radius)
        if t < 1.0:
            outline = _tone("switch_off_border")
            outline.setAlphaF(1.0 - t)
            painter.setPen(outline)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        knob = 12.0
        left, right = 5.0 + knob / 2, self.width() - 5.0 - knob / 2
        centre = left + (right - left) * t
        base = _tone("on_accent") if t > 0.5 else _tone("switch_off_border")
        painter.setBrush(base)
        painter.drawEllipse(QRectF(centre - knob / 2, (self.height() - knob) / 2, knob, knob))


def _blank_icon(size: int = 16) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    return QIcon(pixmap)


class Dropdown(QPushButton):
    """A select box whose list unfolds from its edge over DROPDOWN_DURATION."""

    value_changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Dropdown")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._options = options
        self._value = value
        self._disabled: set[str] = set()
        self._menu: AnimatedMenu | None = None
        self.setMinimumWidth(150)
        self._refresh_text()
        self.clicked.connect(self._open)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if value == self._value:
            return
        self._value = value
        self._refresh_text()

    def set_options(self, options: list[tuple[str, str]], value: str, disabled=()) -> None:
        """Replace the choices (and the selected one) without announcing a change; `disabled` keys are listed but grey."""
        self._options = options
        self._value = value
        self._disabled = set(disabled)
        self._refresh_text()

    def _refresh_text(self) -> None:
        self.setText(next((label for label, key in self._options if key == self._value), ""))
        longest = max((self.fontMetrics().horizontalAdvance(label) for label, _ in self._options), default=0)
        self.setMinimumWidth(max(150, longest + 12 + 30 + 4))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_icon(
            painter, "chevron-down",
            QRectF(self.width() - 22, (self.height() - 10) / 2, 10, 10),
            _tone("text"), stroke=2.2,
        )

    def _set_open(self, is_open: bool) -> None:
        self.setProperty("open", is_open)
        self.style().unpolish(self)
        self.style().polish(self)

    def _open(self) -> None:
        if self._menu is not None and self._menu.isVisible():
            self._menu.close()
            return
        menu = AnimatedMenu(self, duration=DROPDOWN_DURATION, slide=DROPDOWN_SLIDE)
        menu.setMinimumWidth(self.width())
        for label, key in self._options:
            action = menu.addAction(icon("dot", None, 8) if key == self._value else _blank_icon(), label)
            action.setEnabled(key not in self._disabled)
            action.triggered.connect(lambda _checked=False, key=key: self._choose(key))
        menu.aboutToHide.connect(lambda: self._set_open(False))
        self._menu = menu
        self._set_open(True)
        menu.centre_on(self)  # opens centred under the select box, like the toolbar dropdowns
        menu.popup(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def _choose(self, key: str) -> None:
        if key != self._value:
            self._value = key
            self._refresh_text()
            self.value_changed.emit(key)


class _EdgeFade(QWidget):
    """A band in the window color, opaque at the screen edge and clear towards the middle."""

    def __init__(self, top: bool, parent: QWidget):
        super().__init__(parent)
        self._top = top
        self._band: QPixmap | None = None  # the picture behind this band, faded; rebuilt only when it changes
        self._band_key: tuple | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        if backdrop().translucent():
            return  # Acrylic is a see-through tint: there is nothing opaque to fade the page into
        tone = QColor(theme.palette().chrome)
        gradient = QLinearGradient(0, 0, 0, self.height())
        near, far = (1.0, 0.0) if self._top else (0.0, 1.0)
        for position, alpha in ((0.0, near), (1.0, far)):
            color = QColor(tone)
            color.setAlphaF(alpha)
            gradient.setColorAt(position, color)
        painter = QPainter(self)
        if backdrop().active():
            painter.drawPixmap(0, 0, self._faded_picture())
            return
        painter.fillRect(self.rect(), gradient)

    def _faded_picture(self) -> QPixmap:
        """The window background picture behind this band, opaque at the screen edge and clear towards the middle."""
        ratio = self.devicePixelRatioF()
        key = (self.size(), ratio, backdrop().revision(), self.mapTo(self.window(), QPoint(0, 0)), self._top)
        if self._band is not None and key == self._band_key:
            return self._band
        band = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        band.setDevicePixelRatio(ratio)
        band.fill(Qt.GlobalColor.transparent)
        painter = QPainter(band)
        if not backdrop().paint(painter, self, self.rect()):
            painter.fillRect(self.rect(), QColor(theme.palette().chrome))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        mask = QLinearGradient(0, 0, 0, self.height())
        near, far = (1.0, 0.0) if self._top else (0.0, 1.0)
        mask.setColorAt(0.0, QColor(0, 0, 0, round(255 * near)))
        mask.setColorAt(1.0, QColor(0, 0, 0, round(255 * far)))
        painter.fillRect(self.rect(), mask)
        painter.end()
        self._band, self._band_key = band, key
        return band


class FadingScrollArea(SmoothScrollArea):
    """Scroll area whose top and bottom edge fade out, so the content seems to float in the middle."""

    resized = Signal()
    user_scrolled = Signal()  # wheel, scroll bar or keyboard: the page stops following the category list

    def __init__(self, parent=None):
        super().__init__(parent)
        self.verticalScrollBar().actionTriggered.connect(lambda *_: self.user_scrolled.emit())
        self._fade_top = _EdgeFade(True, self)
        self._fade_bottom = _EdgeFade(False, self)
        theme.signals().changed.connect(lambda *_: (self._fade_top.update(), self._fade_bottom.update()))

    def wheelEvent(self, event) -> None:
        self.user_scrolled.emit()
        super().wheelEvent(event)

    def fade_height(self) -> int:
        return min(FADE_HEIGHT, self.height() // 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.viewport().width()
        height = self.fade_height()
        self._fade_top.setGeometry(0, 0, width, height)
        self._fade_bottom.setGeometry(0, self.height() - height, width, height)
        self._fade_top.raise_()
        self._fade_bottom.raise_()
        self.resized.emit()


CLEAR_CACHE_TEXT = "Delete saved thumbnails. They are generated again the next time they are needed."


class SettingCard(QFrame):
    """One row: glyph, title + description, and a control on the right."""

    def __init__(self, glyph: str | None, title: str, description: str = "", control: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(60 if description else 52)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 10, 16, 10)
        row.setSpacing(16)
        if glyph is not None:
            row.addWidget(Glyph(glyph, 20), 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        texts.addWidget(self.title_label)
        if description:
            self.description_label = QLabel(description)
            self.description_label.setObjectName("CardDesc")
            self.description_label.setWordWrap(True)
            texts.addWidget(self.description_label)
        row.addLayout(texts, 1)
        self.control = control
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)


class SwitchRow(QWidget):
    """'On'/'Off' text followed by the switch."""

    def __init__(self, checked: bool, parent=None):
        super().__init__(parent)
        self.switch = ToggleSwitch(checked)
        self.state = QLabel("On" if checked else "Off")
        self.state.setObjectName("CardState")
        self.state.setMinimumWidth(24)
        self.state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.state)
        layout.addWidget(self.switch)
        self.switch.toggled.connect(lambda on: self.state.setText("On" if on else "Off"))


class AboutCard(QFrame):
    """App name + version, with the session id always shown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._session = str(uuid.uuid4())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(18, 10, 16, 10)
        head_row.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(22))
        logo.setFixedSize(22, 22)
        head_row.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        name = QLabel(APP_TITLE)
        name.setObjectName("CardTitle")
        notice = QLabel("© 2026 NPI Gallery. All rights reserved.")
        notice.setObjectName("CardDesc")
        texts.addWidget(name)
        texts.addWidget(notice)
        head_row.addLayout(texts, 1)
        version = QLabel(__version__)
        version.setObjectName("CardValue")
        head_row.addWidget(version, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(head)

        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 8)
        details.setSpacing(0)
        divider = QFrame()
        divider.setObjectName("SettingDivider")
        divider.setFixedHeight(1)
        details.addWidget(divider)
        session = QLabel(f"Session identifier: {self._session}")
        session.setObjectName("CardTitle")
        session.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        session.setIndent(DETAIL_INDENT)
        session.setMinimumHeight(44)
        details.addWidget(session)
        outer.addWidget(self._details)


class AccentCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        store = prefs()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setMinimumHeight(60)
        row = QHBoxLayout(head)
        row.setContentsMargins(18, 10, 16, 10)
        row.setSpacing(16)
        row.addWidget(Glyph("palette", 20), 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = QLabel("Accent color")
        title.setObjectName("CardTitle")
        description = QLabel(
            "Choose the color used for selections, buttons and highlights across the app. Automatic takes it "
            "from the sidebar picture (gray while there is none)"
        )
        description.setObjectName("CardDesc")
        description.setWordWrap(True)
        texts.addWidget(title)
        texts.addWidget(description)
        row.addLayout(texts, 1)
        self.dropdown = Dropdown(ACCENT_OPTIONS, accent.current_mode())
        self.dropdown.value_changed.connect(lambda value: store.set("accent_mode", value))
        row.addWidget(self.dropdown, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(head)

        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(0)
        divider = QFrame()
        divider.setObjectName("SettingDivider")
        divider.setFixedHeight(1)
        details.addWidget(divider)
        custom = QWidget()
        custom.setMinimumHeight(52)
        custom_row = QHBoxLayout(custom)
        custom_row.setContentsMargins(18, 8, 16, 8)
        custom_row.setSpacing(16)
        self.dot = ColorDot(20)
        custom_row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        custom_title = QLabel("Custom colors")
        custom_title.setObjectName("CardTitle")
        custom_row.addWidget(custom_title, 1)
        self.view_button = QPushButton("View colors")
        self.view_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_button.clicked.connect(self.open_dialog)
        custom_row.addWidget(self.view_button, 0, Qt.AlignmentFlag.AlignVCenter)
        details.addWidget(custom)
        outer.addWidget(self._details)
        self.sync()

    def open_dialog(self) -> None:
        open_accent_dialog(self.window())

    def sync(self) -> None:
        store = prefs()
        mode = accent.current_mode()
        self.dropdown.set_value(mode)
        self.dot.set_color(store.get("accent_color"))
        self._details.setVisible(mode == accent.MODE_MANUAL)


BACKGROUND_OPTIONS = [("None", "none"), ("Picture", "picture")]


class _SliderRow(QWidget):
    """Indented label + slider + live value, used inside an expanded card."""

    def __init__(self, title: str, minimum: int, maximum: int, value: int, suffix: str, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(DETAIL_INDENT, 6, 16, 6)
        row.setSpacing(16)
        label = QLabel(title)
        label.setObjectName("CardTitle")
        self.title_label = label
        row.addWidget(label, 1)
        self.slider = SmoothSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("SettingSlider")
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.setFixedWidth(200)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.slider, 0, Qt.AlignmentFlag.AlignVCenter)
        self._suffix = suffix
        self.value_label = QLabel()
        self.value_label.setObjectName("CardState")
        self.value_label.setMinimumWidth(40)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.value_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.slider.valueChanged.connect(self._show_value)
        self._show_value(value)

    def _show_value(self, value: int) -> None:
        self.value_label.setText(f"{value}{self._suffix}")

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_value(self, value: int) -> None:
        if self.slider.value() != value:
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
            self._show_value(value)


class _OpacityControl(QWidget):
    """Compact slider + live percentage, the right-hand control of a setting card."""

    def __init__(self, value: int, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        self.slider = SmoothSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("SettingSlider")
        self.slider.setRange(theme.MIN_FLOAT_OPACITY, 100)
        self.slider.setValue(value)
        self.slider.setFixedWidth(200)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.slider, 0, Qt.AlignmentFlag.AlignVCenter)
        self.value_label = QLabel()
        self.value_label.setObjectName("CardState")
        self.value_label.setMinimumWidth(40)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.value_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.slider.valueChanged.connect(self._show_value)
        self._show_value(self.slider.value())

    def _show_value(self, value: int) -> None:
        self.value_label.setText(f"{value}%")


class PictureBackgroundCard(QFrame):
    """A picture behind part of the window: choose it, frame it, and set how strongly it is dimmed and blurred.

    Subclasses say which preferences (`PREFIX`_mode / _path / _crop / _dim / _blur) and which crop dialog they use.
    """

    PREFIX = ""
    TITLE = ""
    DESCRIPTION = ""
    CHOOSER_TITLE = "Choose a picture"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        store = prefs()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setMinimumHeight(60)
        row = QHBoxLayout(head)
        row.setContentsMargins(18, 10, 16, 10)
        row.setSpacing(16)
        row.addWidget(Glyph("gallery", 20), 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = QLabel(self.TITLE)
        title.setObjectName("CardTitle")
        description = QLabel(self.DESCRIPTION)
        description.setObjectName("CardDesc")
        description.setWordWrap(True)
        self.description_label = description
        texts.addWidget(title)
        texts.addWidget(description)
        row.addLayout(texts, 1)
        self.dropdown = Dropdown(self._mode_options(), self._shown_mode(store.get(self._key("mode"))))
        self.dropdown.value_changed.connect(self._on_mode)
        row.addWidget(self.dropdown, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(head)

        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(0)
        divider = QFrame()
        divider.setObjectName("SettingDivider")
        divider.setFixedHeight(1)
        details.addWidget(divider)

        picture_row = QWidget()
        self.picture_row = picture_row
        picture_row.setMinimumHeight(52)
        picture_layout = QHBoxLayout(picture_row)
        picture_layout.setContentsMargins(DETAIL_INDENT, 8, 16, 8)
        picture_layout.setSpacing(16)
        picture_title = QLabel("Picture")
        picture_title.setObjectName("CardTitle")
        picture_layout.addWidget(picture_title, 0)
        self.path_label = QLabel()
        self.path_label.setObjectName("CardDesc")
        self.path_label.setMinimumWidth(0)
        picture_layout.addWidget(self.path_label, 1)
        self.choose_button = QPushButton("Choose picture")
        self.choose_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_button.clicked.connect(self.choose_and_crop)  # frame a new picture right away
        picture_layout.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.crop_button = QPushButton("Adjust")
        self.crop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.crop_button.clicked.connect(lambda _checked=False: self.crop())  # `clicked` must not pass its bool as the path
        picture_layout.insertWidget(picture_layout.count() - 1, self.crop_button, 0, Qt.AlignmentFlag.AlignVCenter)
        details.addWidget(picture_row)

        self.dim_row = _SliderRow("Dimming", 0, 100, store.get(self._key("dim")), "%")
        self.dim_row.slider.valueChanged.connect(lambda value: store.set(self._key("dim"), value))
        details.addWidget(self.dim_row)

        self.blur_row = _SliderRow("Blur", 0, MAX_BLUR, store.get(self._key("blur")), "")
        self.blur_row.slider.valueChanged.connect(lambda value: store.set(self._key("blur"), value))
        details.addWidget(self.blur_row)

        outer.addWidget(self._details)
        self.sync()

    def choose(self) -> str | None:
        """Ask for a picture; returns its path, or None if the dialog was cancelled.

        Nothing is saved here: the picture only changes once the crop dialog is confirmed with Done.
        """
        store = prefs()
        current = store.get(self._key("path"))
        start = str(Path(current).parent) if current else str(Path.home() / "Pictures")
        chosen, _filter = QFileDialog.getOpenFileName(self.window(), self.CHOOSER_TITLE, start, IMAGE_FILTER)
        return chosen or None

    def choose_and_crop(self) -> bool:
        """Pick a new picture and frame it; cancelling either step leaves things untouched."""
        path = self.choose()
        return bool(path) and self.crop(path)

    def crop(self, path: str | None = None) -> bool:
        """Frame `path` (a freshly chosen picture, starting uncropped) or, by default, the current one.

        The picture and the framing are saved together and only when Done is pressed; Cancel / Esc
        change nothing. Returns True when the choice was applied.
        """
        store = prefs()
        is_new = path is not None
        path = path or store.get(self._key("path"))
        picture = self._source_pixmap(path)
        if picture is None:
            return False
        start_crop = DEFAULT_CROP if is_new else parse_crop(store.get(self._key("crop")))
        framed = self._frame(picture, path, start_crop)
        if framed is None:
            return False
        store.set(self._key("crop"), format_crop(framed))
        store.set(self._key("path"), path)
        return True

    def _key(self, name: str) -> str:
        return f"{self.PREFIX}_{name}"

    def _mode_options(self) -> list[tuple[str, str]]:
        return BACKGROUND_OPTIONS

    def _shown_mode(self, mode: str) -> str:
        """The mode the dropdown shows for the stored `mode`."""
        return mode

    def _source_pixmap(self, path: str):
        raise NotImplementedError

    def _frame(self, picture, path: str, start_crop: tuple[float, float, float]) -> tuple[float, float, float] | None:
        """Open the crop dialog; the chosen framing, or None when it was cancelled."""
        raise NotImplementedError

    def apply_picture(self, path: str) -> bool:
        """Use `path` as the picture (from a photo's menu): frame it in the crop dialog and,
        once Done is pressed, switch the background on. Cancelling changes nothing."""
        if self._source_pixmap(path) is None:
            warn(self.window(), self.TITLE, "That picture can't be opened.")
            return False
        if not self.crop(path):
            return False
        prefs().set(self._key("mode"), "picture")
        return True

    def _on_mode(self, value: str) -> None:
        store = prefs()
        if value == "picture" and not store.get(self._key("path")):
            # a photo rarely has the shape of the area it fills: frame it right away, and stay off if they back out
            path = self.choose()
            if not path or not self.crop(path):
                self.dropdown.set_value("none")
                return
        store.set(self._key("mode"), value)

    def sync(self) -> None:
        store = prefs()
        mode = store.get(self._key("mode"))
        self.dropdown.set_value(mode)
        path = store.get(self._key("path"))
        self.path_label.setText(Path(path).name if path else "No picture chosen")
        self.dim_row.set_value(store.get(self._key("dim")))
        self.blur_row.set_value(store.get(self._key("blur")))
        self._details.setVisible(mode == "picture")


class SidebarBackgroundCard(PictureBackgroundCard):
    """Picture behind the sidebar."""

    PREFIX = "sidebar_bg"
    TITLE = "Sidebar background"
    DESCRIPTION = "Show a picture behind the sidebar"
    CHOOSER_TITLE = "Choose a sidebar picture"

    def _source_pixmap(self, path: str):
        return source_pixmap(path)

    def _frame(self, picture, path, start_crop):
        sidebar = getattr(self.window(), "_sidebar", None)
        panel = sidebar.background_size() if sidebar is not None else QSize(298, 700)
        dialog = SidebarCropDialog(self.window(), picture, panel, start_crop, path)
        return dialog.crop() if dialog.exec() == dialog.DialogCode.Accepted else None


class WindowBackgroundCard(PictureBackgroundCard):
    """What is behind the whole window: the margins around the sidebar, the top bar and the photos.

    A picture, the blurred desktop wallpaper (Mica, drawn by the app), the look of Windows' Acrylic drawn
    by the app, or the system Acrylic. The system Acrylic falls back to the manual one when Windows cannot give it.
    """

    PREFIX = "window_bg"
    TITLE = "Window background"
    DESCRIPTION = "Show a picture behind the whole window, around the sidebar and behind the photos"
    CHOOSER_TITLE = "Choose a background picture"
    MODE_LABELS = {
        "none": "None",
        "picture": "Picture",
        "mica": "Mica (wallpaper)",
        "acrylic_manual": "Acrylic (manual)",
        "acrylic": "Acrylic (system)",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        store = prefs()
        self.mica_blur_row = _SliderRow("Blur", 0, MAX_MICA_BLUR, store.get("window_mica_blur"), "")
        self.mica_blur_row.slider.valueChanged.connect(lambda value: store.set("window_mica_blur", value))
        self._details.layout().addWidget(self.mica_blur_row)
        self.acrylic_blur_row = _SliderRow("Blur", 0, MAX_MICA_BLUR, store.get("window_acrylic_blur"), "")
        self.acrylic_blur_row.slider.valueChanged.connect(lambda value: store.set("window_acrylic_blur", value))
        self._details.layout().addWidget(self.acrylic_blur_row)
        backdrop().availability_changed.connect(self.sync)  # Transparency effects was switched in Windows
        self.sync()

    def _mode_options(self) -> list[tuple[str, str]]:
        return [(self.MODE_LABELS[mode], mode) for mode in backdrop().supported_modes()]

    def _shown_mode(self, mode: str) -> str:
        if mode in backdrop().disabled_modes() and "acrylic_manual" in backdrop().supported_modes():
            return "acrylic_manual"  # an Acrylic that Windows cannot give now shows as the manual one
        if mode in backdrop().supported_modes():
            return mode
        return "none"

    def _description(self) -> str:
        modes = backdrop().supported_modes()
        if "mica" not in modes:
            return self.DESCRIPTION
        if backdrop().disabled_modes():
            return "Show a picture, your blurred wallpaper (Mica) or Acrylic behind the whole window. Acrylic (system) needs Transparency effects, in Windows settings"
        if "acrylic" in modes:
            return "Show a picture, your blurred wallpaper (Mica) or Acrylic behind the whole window"
        return "Show a picture, your blurred wallpaper (Mica) or an Acrylic look behind the whole window"

    def sync(self) -> None:
        store = prefs()
        mode = self._shown_mode(store.get(self._key("mode")))
        self.dropdown.set_options(self._mode_options(), mode, backdrop().disabled_modes())
        self.description_label.setText(self._description())
        path = store.get(self._key("path"))
        self.path_label.setText(Path(path).name if path else "No picture chosen")
        self.dim_row.set_value(store.get(self._key("dim")))
        self.dim_row.set_title("Dimming" if mode == "picture" else "Tint")
        self.blur_row.set_value(store.get(self._key("blur")))
        if hasattr(self, "mica_blur_row"):  # sync() also runs once from the base constructor, before these rows exist
            self.mica_blur_row.set_value(store.get("window_mica_blur"))
            self.mica_blur_row.setVisible(mode == "mica")
            self.acrylic_blur_row.set_value(store.get("window_acrylic_blur"))
            self.acrylic_blur_row.setVisible(mode == "acrylic_manual")
        self.picture_row.setVisible(mode == "picture")
        self.blur_row.setVisible(mode == "picture")
        self._details.setVisible(mode != "none")

    def _source_pixmap(self, path: str):
        return backdrop().source_pixmap(path)

    def _frame(self, picture, path, start_crop):
        measure = getattr(self.window(), "backdrop_size", None)
        window = measure() if callable(measure) else QSize(1360, 860)
        dialog = BackdropCropDialog(self.window(), picture, window, start_crop, path)
        return dialog.crop() if dialog.exec() == dialog.DialogCode.Accepted else None


class HiddenFoldersDialog(FloatDialog):
    """Dialog showing hidden subfolders with a Restore button for each one."""

    restore_requested = Signal(object)  # Path

    def __init__(self, folders: list[Path], parent=None):
        super().__init__("Hidden folders", parent, width=560)
        self.body.addWidget(
            dialog_label(
                "These folders and their photos are hidden from the gallery. Restore a folder to show it again.",
                "detail",
                wrap=True,
            )
        )

        self._scroll = SmoothScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMaximumHeight(320)

        container = QWidget()
        self._rows_layout = QVBoxLayout(container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._scroll.setWidget(container)
        self.body.addWidget(self._scroll)

        self._empty_label = dialog_label("No hidden folders", "detail")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setMinimumHeight(72)
        self.body.addWidget(self._empty_label)

        self.set_folders(folders)
        self.finish()

    def set_folders(self, folders: list[Path]) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        has_items = bool(folders)
        self._scroll.setVisible(has_items)
        self._empty_label.setVisible(not has_items)

        for folder in folders:
            row_card = QFrame()
            row_card.setObjectName("SettingCard")
            row_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row = QHBoxLayout(row_card)
            row.setContentsMargins(14, 10, 14, 10)
            row.setSpacing(12)
            row.addWidget(Glyph("folder", 18), 0, Qt.AlignmentFlag.AlignVCenter)

            texts = QVBoxLayout()
            texts.setSpacing(1)
            name_label = QLabel(folder.name or str(folder))
            name_label.setObjectName("CardTitle")
            path_label = QLabel(str(folder))
            path_label.setObjectName("CardDesc")
            path_label.setWordWrap(True)
            texts.addWidget(name_label)
            texts.addWidget(path_label)
            row.addLayout(texts, 1)

            restore_btn = make_button("Restore", "secondary")
            restore_btn.clicked.connect(lambda _checked=False, p=folder: self.restore_requested.emit(p))
            row.addWidget(restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)

            self._rows_layout.addWidget(row_card)

        if has_items:
            row_height = 60
            visible_rows = min(len(folders), 5)
            self._scroll.setFixedHeight(visible_rows * row_height + max(0, visible_rows - 1) * 6)
        self.refit()


class SettingsPage(QWidget):
    """Category list on the left; on the right ONE long column with every category, one under the other.

    The list works as an index: a click glides the column so that category sits on the sidebar's middle
    line, and while you scroll the list highlights the category that is on that line."""

    clear_cache_requested = Signal()
    dump_requested = Signal()
    restore_requested = Signal()
    restore_folder_requested = Signal(object)
    find_duplicates_requested = Signal()
    review_duplicates_requested = Signal()
    reset_ignored_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Content")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        p = prefs()
        self._hidden_folders: list[Path] = []
        self._hidden_dialog: HiddenFoldersDialog | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav = QWidget()
        self._nav.setObjectName("SettingsNav")
        self._nav.setFixedWidth(NAV_WIDTH)
        nav_layout = QVBoxLayout(self._nav)
        nav_layout.setContentsMargins(12, 12, 12, 12)  # the top margin is set by _place_nav
        self._nav_layout = nav_layout
        self._center_ref: QWidget | None = None
        self._nav_list = NavList()
        for key, label, glyph in CATEGORIES:
            self._nav_list.add_item(key, glyph, label)
        self._nav_list.selected.connect(self.show_category)
        self._nav_items = self._nav_list.items
        nav_layout.addWidget(self._nav_list)
        nav_layout.addStretch(1)
        root.addWidget(self._nav)

        self._pages: dict[str, QWidget] = {}  # category key -> its section in the column
        self._centre = 0  # the sidebar's middle line, in page (= scroll viewport) coordinates
        self._navigating = False  # a click on the list is scrolling the column: do not re-select while it glides
        self._layout_pending = False
        self._scroll = FadingScrollArea()
        self._scroll.setObjectName("SettingsScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SettingsBody")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(32, 0, 32, 0)  # top / bottom are set by _layout_sections
        self._body_layout.setSpacing(SECTION_GAP)
        self._scroll.setWidget(body)
        # laid out at once (not on the next loop turn): PageTransition grabs this page as a pixmap before it is
        # ever shown, and a deferred layout would leave that first picture with the initial 0 top margin
        self._scroll.resized.connect(self._layout_sections)
        self._scroll.user_scrolled.connect(self._on_user_scrolled)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        root.addWidget(self._scroll, 1)

        # Personalization
        self._begin_page("personalization")
        self.theme_dropdown = Dropdown(THEME_OPTIONS, p.get("theme"))
        self.theme_dropdown.value_changed.connect(lambda value: p.set("theme", value))
        self._card("palette", "Customize theme", "Choose a color theme to give NPI Gallery more personality", self.theme_dropdown)

        self.accent_card = AccentCard()
        self._column.addWidget(self.accent_card)

        self.window_background_card = WindowBackgroundCard()
        self._column.addWidget(self.window_background_card)

        self.float_opacity_control = _OpacityControl(p.get("float_opacity"))
        self.float_opacity_control.slider.valueChanged.connect(lambda value: p.set("float_opacity", value))
        self._card(
            "adjust", "Floating panel opacity",
            "How see-through the floating panels and menus are, in the gallery and the photo viewer",
            self.float_opacity_control,
        )

        self.background_card = SidebarBackgroundCard()
        self._column.addWidget(self.background_card)
        self._end_page()

        # General: things that apply to every kind of media
        self._begin_page("general")
        self.location_switch = SwitchRow(p.get("location_features"))
        self.location_switch.switch.toggled.connect(lambda on: p.set("location_features", on))
        self._card(
            "location", "Enable location based features",
            "Show GPS coordinates from photo metadata in the info panel", self.location_switch,
        )

        self.confirm_switch = SwitchRow(p.get("confirm_delete"))
        self.confirm_switch.switch.toggled.connect(lambda on: p.set("confirm_delete", on))
        self._card("trash", "Ask for permission to delete photos", "Show a confirmation dialog when deleting items", self.confirm_switch)

        self.tile_switch = SwitchRow(p.get("tile_attributes"))
        self.tile_switch.switch.toggled.connect(lambda on: p.set("tile_attributes", on))
        self._card("tile-edit", "Show gallery tile attributes", "Show the file format on gallery tiles", self.tile_switch)

        self.hidden_folders_button = QPushButton("View folders")
        self.hidden_folders_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hidden_folders_button.setEnabled(False)
        self.hidden_folders_button.clicked.connect(self.open_hidden_folders_dialog)
        self.hidden_folders_card = self._card(
            "eye", "Hidden folders", "No hidden folders", self.hidden_folders_button
        )

        self.open_with_button = QPushButton()
        self.open_with_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_with_button.clicked.connect(self._toggle_open_with)
        self.open_with_card = self._card(
            "image-open", "Open with", "Show NPI Gallery in the Open with list of Explorer", self.open_with_button
        )
        self.open_with_card.setVisible(shell_registration.is_supported())
        self._refresh_open_with()

        self._end_page()

        # Photos
        self._begin_page("photos")
        self._section("Photo viewer")
        self.wheel_dropdown = Dropdown(WHEEL_OPTIONS, p.get("mouse_wheel"))
        self.wheel_dropdown.value_changed.connect(lambda value: p.set("mouse_wheel", value))
        self._card(
            "mouse", "Mouse Wheel",
            "Choose mouse wheel behavior when viewing a single file (Note: Ctrl + mouse wheel always zooms in or out)",
            self.wheel_dropdown,
        )

        self.zoom_dropdown = Dropdown(ZOOM_OPTIONS, p.get("zoom_preference"))
        self.zoom_dropdown.value_changed.connect(lambda value: p.set("zoom_preference", value))
        self._card(
            "image-zoom", "Zoom preference",
            "Choose how to open and view photos that are smaller than your window", self.zoom_dropdown,
        )

        self.autohide_switch = SwitchRow(p.get("auto_hide_controls"))
        self.autohide_switch.switch.toggled.connect(lambda on: p.set("auto_hide_controls", on))
        self._card(
            "eye", "Auto-hide controls in photo viewer",
            "Fade out the toolbars, zoom bar and filmstrip after a few seconds without mouse movement",
            self.autohide_switch,
        )

        self.zoom_hide_switch = SwitchRow(p.get("hide_controls_on_zoom"))
        self.zoom_hide_switch.switch.toggled.connect(lambda on: p.set("hide_controls_on_zoom", on))
        self._card(
            "image-zoom", "Auto-hide controls while zooming",
            "Hide the controls in the photo viewer while a photo is zoomed with the mouse wheel or a double-click; move the mouse to the top or bottom edge to bring them back",
            self.zoom_hide_switch,
        )
        self._section("Gallery", gap=24)
        self.gif_dropdown = Dropdown(GIF_OPTIONS, p.get("gif_autoplay"))
        self.gif_dropdown.value_changed.connect(lambda value: p.set("gif_autoplay", value))
        self._card(
            "gallery", "Play animated GIFs",
            "Animate GIF thumbnails in the gallery: never, only the one under the mouse, or every GIF on screen. "
            "Animations pause while the window is in the background",
            self.gif_dropdown,
        )
        self._end_page()

        # Video
        self._begin_page("video")
        self._section("Playback")
        self.seek_dropdown = Dropdown(SEEK_OPTIONS, p.get("video_seek_seconds"))
        self.seek_dropdown.value_changed.connect(lambda value: p.set("video_seek_seconds", value))
        self._card(
            "video", "Skip forward / back",
            "How far a double-click on the left or right edge of a video skips. A single click there goes to "
            "the previous or next item",
            self.seek_dropdown,
        )
        options = hwaccel_options()
        saved = p.get("video_hwaccel")
        self.hwaccel_dropdown = Dropdown(options, saved if any(key == saved for _label, key in options) else hwdecode.AUTO)
        self.hwaccel_dropdown.value_changed.connect(self._on_hwaccel)
        self._card(
            "video", "Hardware acceleration",
            "Decode videos on the graphics processor. Automatic lets the system choose; pick a GPU to run "
            "NPI Gallery on it (useful on laptops with integrated and dedicated graphics)",
            self.hwaccel_dropdown,
        )
        self.restart_button = QPushButton("Restart")
        self.restart_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_button.clicked.connect(self._restart)
        self.restart_card = self._card(
            "info", "Restart to apply", "The new hardware acceleration setting is used the next time NPI Gallery starts",
            self.restart_button,
        )
        self.restart_card.setVisible(False)
        self._end_page()

        # Files: convert whole folders to .npi and back
        self._begin_page("files")
        self.dump_button = QPushButton("Dump folder...")
        self.dump_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dump_button.clicked.connect(self.dump_requested)
        self._card(
            "export", "Dump folder to .npi",
            "Convert the images in a folder (and its subfolders) to .npi files", self.dump_button,
        )
        self.restore_button = QPushButton("Restore folder...")
        self.restore_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_button.clicked.connect(self.restore_requested)
        self._card(
            "import", "Restore folder from .npi",
            "Turn the .npi files in a folder (and its subfolders) back into regular images", self.restore_button,
        )

        self._npi_reverting = False
        self.npi_switch = SwitchRow(shell_registration.is_npi_registered())
        self.npi_switch.switch.toggled.connect(self._toggle_npi_open_with)
        self.npi_card = self._card(
            "box", "Quick open in file manager",
            "Open .npi files in NPI Gallery from the file manager. Off: .npi files are not linked to the app",
            self.npi_switch,
        )
        self.npi_card.setVisible(shell_registration.is_supported())
        self._end_page()

        # Storage
        self._begin_page("storage")
        self.clear_button = QPushButton("Clear cache")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self._on_clear)
        self.clear_card = self._card(
            "broom", "Clear thumbnail cache", CLEAR_CACHE_TEXT, self.clear_button,
        )

        self._section("Duplicate photos", gap=24)
        self.review_duplicates_button = QPushButton("Review")
        self.review_duplicates_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.review_duplicates_button.setEnabled(False)
        self.review_duplicates_button.clicked.connect(self.review_duplicates_requested)
        self.find_duplicates_button = QPushButton("Find duplicates")
        self.find_duplicates_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.find_duplicates_button.clicked.connect(self.find_duplicates_requested)
        duplicate_buttons = QWidget()
        duplicate_row = QHBoxLayout(duplicate_buttons)
        duplicate_row.setContentsMargins(0, 0, 0, 0)
        duplicate_row.setSpacing(8)
        duplicate_row.addWidget(self.review_duplicates_button)
        duplicate_row.addWidget(self.find_duplicates_button)
        self.duplicates_card = self._card(
            "copy", "Find duplicate photos", DUPLICATES_TEXT, duplicate_buttons,
        )
        self.reset_ignored_button = QPushButton("Reset")
        self.reset_ignored_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_ignored_button.clicked.connect(self.reset_ignored_requested)
        self.ignored_card = self._card(
            "eye", "Ignored duplicates", IGNORED_TEXT, self.reset_ignored_button,
        )
        self.reset_ignored_button.setEnabled(False)
        self._end_page()

        # About
        self._begin_page("about")
        self.about = AboutCard()
        self._column.addWidget(self.about)
        self._section("About NPI Gallery", gap=16)
        for glyph, title, text in ABOUT_APP:
            self._card(glyph, title, text, None)
        self._section("Third party software", gap=16)
        for name, version, license_name, purpose, site in third_party():
            value = QLabel(license_name)
            value.setObjectName("CardValue")
            self._card(None, f"{name} {version}".strip(), f"{purpose}\n{site}", value)
        self._section("Licenses", gap=16)
        self._card(
            None, "Each component keeps its own license",
            "The programs above belong to their authors and are used under the licenses named next to them. "
            "Their full texts are on the websites listed under each entry; the Inter license is also in "
            "npigallery/assets/fonts. Qt and FFmpeg are used as separate libraries.",
            None,
        )
        self._end_page()

        self._nav_list.set_current(CATEGORIES[0][0], animate=False)
        p.changed.connect(self._sync_from_prefs)

    # the category list is centred on the sidebar (the tall card on the left of the window), not on this page

    def set_center_reference(self, widget: QWidget) -> None:
        """Centre the category list vertically on `widget` (the main window's sidebar)."""
        if self._center_ref is not None:
            self._center_ref.removeEventFilter(self)
        self._center_ref = widget
        widget.installEventFilter(self)
        self._place_nav()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._center_ref and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._place_nav()
        elif event.type() == QEvent.Type.Resize and any(watched is page for page in self._pages.values()):
            self._request_layout()  # a card opened or closed: the section changed height
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_nav()

    def _place_nav(self) -> None:
        height = self._nav_list.sizeHint().height()
        ref = self._center_ref
        if ref is not None and ref.window() is self.window():
            window = self.window()
            centre = ref.mapTo(window, ref.rect().center()).y() - self.mapTo(window, QPoint(0, 0)).y()
        else:
            centre = self.height() // 2
        top = max(12, min(centre - height // 2, self.height() - height - 12))
        self._nav_layout.setContentsMargins(12, top, 12, 12)
        self._centre = centre
        self._request_layout()

    # categories: one long column, the list on the left is its index

    def category_keys(self) -> list[str]:
        return [key for key, _label, _glyph in CATEGORIES]

    def current_category(self) -> str:
        return self._nav_list.current or CATEGORIES[0][0]

    def show_category(self, key: str) -> None:
        """Glide the column so that category's section sits on the middle line."""
        if key not in self._pages:
            return
        self._nav_list.set_current(key)
        self._navigating = True
        self._scroll.scroll_to(self._section_target(key))

    def _edge_gap(self) -> int:
        """Room kept under the top dimming for a section that is too tall to centre."""
        return min(self._scroll.fade_height(), self._scroll.viewport().height() // 5)

    def _is_tall(self, widget: QWidget) -> bool:
        return widget.height() > self._scroll.viewport().height() - 2 * self._edge_gap()

    def _section_target(self, key: str) -> int:
        """Scroll value that puts the section on the middle line (a tall one starts under the dimming)."""
        widget = self._pages[key]
        if self._is_tall(widget):
            return widget.y() - self._edge_gap()
        return round(widget.y() + widget.height() / 2 - self._centre)

    def _request_layout(self, *_args) -> None:
        if not self._layout_pending:
            self._layout_pending = True
            QTimer.singleShot(0, self._layout_sections)

    def _layout_sections(self) -> None:
        """Empty room above the first and below the last section, just enough for each to reach the middle line."""
        self._layout_pending = False
        if not self._pages or self._scroll.viewport().height() <= 0:
            return
        self._body_layout.activate()
        height = self._scroll.viewport().height()
        gap = self._edge_gap()
        sections = list(self._pages.values())
        first, last = sections[0], sections[-1]
        top = gap if self._is_tall(first) else max(0, round(self._centre - first.height() / 2))
        if self._is_tall(last):
            bottom = max(0, height - gap - last.height())
        else:
            bottom = max(0, round(height - self._centre - last.height() / 2))
        bottom = max(bottom, self._scroll.fade_height())  # the end of the last section can leave the bottom dimming
        self._body_layout.setContentsMargins(32, top, 32, bottom)
        self._body_layout.activate()  # the sections moved: read their new positions
        self._on_scrolled(self._scroll.verticalScrollBar().value())

    def _on_user_scrolled(self) -> None:
        self._navigating = False

    def _on_scrolled(self, value: int) -> None:
        """Highlight the category that is on the middle line."""
        if self._navigating or not self._pages:
            return
        line = value + self._centre
        best, best_distance = None, None
        for key, widget in self._pages.items():
            top, bottom = widget.y(), widget.y() + widget.height()
            distance = 0 if top <= line < bottom else min(abs(line - top), abs(line - bottom))
            if best_distance is None or distance < best_distance:
                best, best_distance = key, distance
        if best is not None and best != self._nav_list.current:
            self._nav_list.set_current(best)

    def prepare_for_display(self) -> None:
        """Bring a page that was never shown to its final layout (PageTransition calls this before it grabs the page).

        The first grab flushes the pending resize / polish events of the hidden page; then the sections are laid out
        again until nothing moves, so the picture matches what the shown page settles into."""
        self.grab(QRect(0, 0, 1, 1))
        last = None
        for _ in range(4):
            for widget in self.findChildren(QWidget):
                widget.ensurePolished()
            self._place_nav()
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            self._layout_sections()
            margins = self._body_layout.contentsMargins()
            state = (margins.top(), margins.bottom(), self._centre, tuple(page.height() for page in self._pages.values()))
            if state == last:
                break
            last = state

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_nav()
        # lay the sections out right now, not on the next event-loop turn: otherwise the first frame is painted
        # with the initial 0 top margin and the content jumps down a few ms later
        self._layout_sections()

    def _begin_page(self, key: str) -> None:
        """Start a category: a title, then its cards, as one section of the long column."""
        label = next(text for k, text, _glyph in CATEGORIES if k == key)
        self._section_key = key
        self._section_widget = QWidget()
        self._section_widget.setObjectName("SettingsBody")
        self._column = QVBoxLayout(self._section_widget)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(2)
        title = QLabel(label)
        title.setObjectName("SettingsPageTitle")
        self._column.addWidget(title)
        self._column.addSpacing(10)

    def _end_page(self) -> None:
        holder = self._centered(self._section_widget, (0, 0, 0, 0))
        holder.installEventFilter(self)
        self._body_layout.addWidget(holder)
        self._pages[self._section_key] = holder

    @staticmethod
    def _centered(inner: QWidget, margins: tuple[int, int, int, int]) -> QWidget:
        holder = QWidget()
        holder.setObjectName("SettingsBody")
        row = QHBoxLayout(holder)
        row.setContentsMargins(*margins)
        inner.setMaximumWidth(COLUMN_MAX_WIDTH)
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addStretch(1)
        row.addWidget(inner, 1000)
        row.addStretch(1)
        return holder

    def _section(self, text: str, gap: int = 0) -> None:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        if gap:
            self._column.addSpacing(gap)
        self._column.addWidget(label)
        self._column.addSpacing(6)

    def _card(self, glyph: str, title: str, description: str, control: QWidget) -> SettingCard:
        card = SettingCard(glyph, title, description, control)
        self._column.addWidget(card)
        return card

    def _refresh_open_with(self) -> None:
        registered = shell_registration.is_registered()
        self.open_with_button.setText("Remove" if registered else "Add")
        self.open_with_card.description_label.setText(
            "NPI Gallery is listed in Open with for photos and videos"
            if registered
            else "Show NPI Gallery in the Open with list of Explorer for photos and videos"
        )

    def _toggle_open_with(self) -> None:
        try:
            if shell_registration.is_registered():
                shell_registration.unregister()
            else:
                shell_registration.register()
        except shell_registration.RegistrationError as error:
            warn(self.window(), "Open with", str(error))
        self._refresh_open_with()

    def _toggle_npi_open_with(self, on: bool) -> None:
        if self._npi_reverting:
            return
        try:
            shell_registration.set_npi_registered(on)
        except shell_registration.RegistrationError as error:
            warn(self.window(), "Open with", str(error))
            self._npi_reverting = True
            self.npi_switch.switch.setChecked(not on)
            self._npi_reverting = False

    def _on_hwaccel(self, value: str) -> None:
        prefs().set("video_hwaccel", value)
        hwdecode.apply_selection(value)  # the Windows GPU entry is written now; the decoder is read at start
        self._update_restart_notice()

    def _update_restart_notice(self) -> None:
        self.restart_card.setVisible(self.hwaccel_dropdown.value() != hwdecode.started_with())

    def _restart(self) -> None:
        if QProcess.startDetached(sys.executable, hwdecode.restart_arguments()):
            QApplication.quit()

    def set_cache_usage(self, count: int, size: int) -> None:
        """Show how much the thumbnail cache holds, right in the Clear card's description."""
        if count <= 0:
            usage = "No thumbnails are saved right now."
        else:
            noun = "thumbnail" if count == 1 else "thumbnails"
            usage = f"Saved thumbnails take {human_size(size)} ({count:,} {noun})."
        self.clear_card.description_label.setText(f"{usage} {CLEAR_CACHE_TEXT}")

    def set_duplicate_summary(self, groups: int, freeable: int, scanned: bool) -> None:
        """Say what the last scan found, in the card's description; Review is only enabled with something to review."""
        if groups:
            noun = "group" if groups == 1 else "groups"
            text = f"{groups} {noun} of identical photos. Removing the copies frees {human_size(freeable)}."
        elif scanned:
            text = "No identical photos were found."
        else:
            text = DUPLICATES_TEXT
        self.duplicates_card.description_label.setText(text)
        self.review_duplicates_button.setEnabled(groups > 0)

    def set_duplicate_progress(self, text: str | None) -> None:
        """While scanning: a progress line in the description and the button locked; None when finished."""
        busy = text is not None
        self.find_duplicates_button.setEnabled(not busy)
        self.find_duplicates_button.setText("Scanning..." if busy else "Find duplicates")
        if busy:
            self.duplicates_card.description_label.setText(text)

    def set_ignored_count(self, count: int) -> None:
        if count:
            noun = "entry" if count == 1 else "entries"
            self.ignored_card.description_label.setText(
                f"{count} {noun} marked as not a duplicate. Reset to let them be found again."
            )
        else:
            self.ignored_card.description_label.setText(IGNORED_TEXT)
        self.reset_ignored_button.setEnabled(count > 0)

    def set_hidden_folders(self, folders: list[Path]) -> None:
        """Update the Hidden folders card (and open dialog, if any) in Settings > General."""
        self._hidden_folders = list(folders)
        count = len(self._hidden_folders)
        if count == 0:
            self.hidden_folders_card.description_label.setText("No hidden folders")
            self.hidden_folders_button.setEnabled(False)
        else:
            noun = "folder is" if count == 1 else "folders are"
            self.hidden_folders_card.description_label.setText(f"{count} {noun} hidden from the gallery")
            self.hidden_folders_button.setEnabled(True)
        if self._hidden_dialog is not None:
            self._hidden_dialog.set_folders(self._hidden_folders)

    def open_hidden_folders_dialog(self) -> None:
        dialog = HiddenFoldersDialog(self._hidden_folders, self.window())
        dialog.restore_requested.connect(self.restore_folder_requested)
        self._hidden_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._hidden_dialog = None
            dialog.deleteLater()

    def _on_clear(self) -> None:
        self.clear_cache_requested.emit()
        self.clear_button.setText("Cleared")
        self.clear_button.setEnabled(False)
        QTimer.singleShot(1800, self._reset_clear_button)

    def _reset_clear_button(self) -> None:
        self.clear_button.setText("Clear cache")
        self.clear_button.setEnabled(True)

    def _sync_from_prefs(self, key: str) -> None:
        """Keep the controls right if a preference changes from somewhere else."""
        p = prefs()
        if key == "theme":
            self.theme_dropdown.set_value(p.get("theme"))
        elif key in accent.WATCHED_KEYS:
            self.accent_card.sync()
        elif key.startswith("sidebar_bg_"):
            self.background_card.sync()
        elif key.startswith(("window_bg_", "window_mica_", "window_acrylic_")):
            self.window_background_card.sync()
        elif key == "mouse_wheel":
            self.wheel_dropdown.set_value(p.get("mouse_wheel"))
        elif key == "zoom_preference":
            self.zoom_dropdown.set_value(p.get("zoom_preference"))
        elif key == "video_seek_seconds":
            self.seek_dropdown.set_value(p.get("video_seek_seconds"))
        elif key == "video_hwaccel":
            self.hwaccel_dropdown.set_value(p.get("video_hwaccel"))
            self._update_restart_notice()
        else:
            switches = {
                "tile_attributes": self.tile_switch,
                "location_features": self.location_switch,
                "confirm_delete": self.confirm_switch,
                "auto_hide_controls": self.autohide_switch,
                "hide_controls_on_zoom": self.zoom_hide_switch,
            }
            row = switches.get(key)
            if row is not None and row.switch.isChecked() != p.get(key):
                row.switch.setChecked(p.get(key))