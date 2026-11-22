"""Floating glass controls drawn over the photo."""

from PySide6.QtCore import QAbstractAnimation, QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QWidget

from ..common.icons import draw_icon
from ..theme import theme
from ..common.motion import DURATION_FAST, DURATION_NORMAL, accelerate, decelerate, make_animation
from . import geometry as geo
from .style import (
    GLASS,
    GLASS_BORDER,
    HEART,
    HOVER,
    ICON,
    PRESSED,
    SOFT,
    TEXT,
    TEXT_DIM,
    accent_pair,
    floating,
)


def _font(widget: QWidget, pixels: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(widget.font())
    font.setPixelSize(pixels)
    font.setWeight(weight)
    return font


class GlassPanel(QWidget):
    """Rounded, translucent plate (fully rounded ends unless a radius is given)."""

    def __init__(self, parent=None, radius: float | None = None, fill: QColor = GLASS):
        super().__init__(parent)
        self._radius = radius
        self._fill = fill
        theme.signals().changed.connect(self.update)  # repaint when the floating opacity changes

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self._radius if self._radius is not None else min(rect.width(), rect.height()) / 2
        painter.setPen(QPen(GLASS_BORDER, 1))
        painter.setBrush(floating(self._fill))
        painter.drawRoundedRect(rect, radius, radius)


class IconButton(QAbstractButton):
    """Round icon button. `checked_bg` fills it with the accent when checked (Info);
    `checked_color` draws a filled glyph in that color when checked (the heart)."""

    def __init__(
        self,
        icon_name: str,
        size: int = 36,
        glyph: int = 18,
        parent=None,
        checked_bg: bool = False,
        checked_color: QColor | None = None,
        solid_bg: bool = False,
        hover_bg: QColor | None = None,
        filled: bool = False,
    ):
        super().__init__(parent)
        self._filled = filled
        self._hover_bg = hover_bg
        self._icon = icon_name
        self._glyph = glyph
        self._checked_bg = checked_bg
        self._checked_color = checked_color
        self._solid_bg = solid_bg
        if solid_bg:
            theme.signals().changed.connect(self.update)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_icon(self, name: str) -> None:
        self._icon = name
        self.update()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:  # greyed out buttons are not clickable: no hand
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        accent = self._checked_bg and self.isChecked()
        accent_color, on_accent = accent_pair()
        icon_color = QColor(on_accent if accent else ICON)
        filled = self._filled
        if self._checked_color is not None and self.isChecked():
            icon_color, filled = QColor(self._checked_color), True
        background = None
        if accent:
            background = QColor(accent_color)
            if self.isDown():
                background = background.darker(115)
        elif self.isDown():
            background = self._hover_bg.darker(115) if self._hover_bg is not None else PRESSED
        elif self.underMouse() and enabled:
            background = self._hover_bg if self._hover_bg is not None else HOVER
        elif self._solid_bg:
            background = floating(GLASS)
        if background is not None:
            painter.setPen(QPen(GLASS_BORDER, 1) if self._solid_bg and not accent else Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        if not enabled:
            painter.setOpacity(0.35)  # draw_icon drops a color's alpha, so dim the painter instead
        side = float(self._glyph)
        glyph = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        draw_icon(painter, self._icon, glyph, icon_color, filled=filled, stroke=1.9)


class PillButton(QAbstractButton):
    """Rounded-rectangle button with an optional icon and text ("Edit", the "80%" zoom label)."""

    def __init__(self, text: str, icon_name: str | None = None, width: int | None = None, parent=None, height: int = 36):
        super().__init__(parent)
        self._icon = icon_name
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._always_filled = icon_name is not None
        self._fixed_width = width
        self.setFixedHeight(height)
        self.setFixedWidth(width if width else self._natural_width())

    def _natural_width(self) -> int:
        metrics = QFontMetrics(_font(self, 13, QFont.Weight.DemiBold))
        return metrics.horizontalAdvance(self.text()) + 28 + (26 if self._icon else 0)

    def set_label(self, text: str) -> None:
        self.setText(text)
        if not self._fixed_width:
            self.setFixedWidth(self._natural_width())
        self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isDown():
            background = PRESSED
        elif self.underMouse() and self.isEnabled():
            background = HOVER
        else:
            background = SOFT if self._always_filled else None
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setFont(_font(self, 13, QFont.Weight.DemiBold))
        if not self.isEnabled():
            painter.setOpacity(0.35)  # greyed out: this action does not apply to the current item
        painter.setPen(TEXT)
        text_rect = QRectF(self.rect())
        if self._icon:
            draw_icon(painter, self._icon, QRectF(13, (self.height() - 16) / 2, 16, 16), ICON, stroke=1.9)
            text_rect.adjust(32, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())


class ElidedLabel(QWidget):
    def __init__(
        self,
        parent,
        pixels: int,
        weight: QFont.Weight,
        color: QColor,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    ):
        super().__init__(parent)
        self._text = ""
        self._color = color
        self._align = align
        self.setFont(_font(self, pixels, weight))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def text_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance(self._text)

    def line_height(self) -> int:
        return QFontMetrics(self.font()).height()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self._color)
        elided = QFontMetrics(self.font()).elidedText(self._text, Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | self._align, elided)


class TitlePill(GlassPanel):
    """File name + date, centred in the top row."""

    PAD = 18

    def __init__(self, parent=None, height: int = 46):
        super().__init__(parent)
        self._height = height
        center = Qt.AlignmentFlag.AlignHCenter
        self._name = ElidedLabel(self, 14, QFont.Weight.DemiBold, TEXT, center)
        self._date = ElidedLabel(self, 12, QFont.Weight.Normal, TEXT_DIM, center)
        self.setProperty("dragRegion", True)  # an empty part of the top row moves the window
        self.setFixedHeight(height)
        self.fit(300)

    def set_texts(self, name: str, date: str) -> None:
        self._name.set_text(name)
        self._date.set_text(date)

    def natural_width(self) -> int:
        text = max(self._name.text_width(), self._date.text_width(), 60)
        return text + 2 + 2 * self.PAD

    def fit(self, width: int) -> None:
        """Make the pill `width` wide (long names are elided) and centre both lines in it."""
        width = max(2 * self.PAD + 10, width)
        text = width - 2 * self.PAD
        self.setFixedWidth(width)
        name_h, date_h = self._name.line_height(), self._date.line_height()
        top = (self._height - name_h - date_h) // 2
        self._name.setGeometry(self.PAD, top, text, name_h)
        self._date.setGeometry(self.PAD, top + name_h, text, date_h)
        self.update()


class Separator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
        x = self.width() // 2
        painter.drawLine(x, 9, x, self.height() - 9)


def _icon_button(
    key: str, icon_name: str, checkable: bool, parent: QWidget, signal: Signal, accent: bool = False
) -> IconButton:
    button = IconButton(icon_name, 36, 18, parent, checked_bg=accent, checked_color=HEART if key == "favorite" else None)
    button.setCheckable(checkable)
    button.clicked.connect(lambda _=False, name=key: signal.emit(name))
    return button


class ToolsPill(GlassPanel):
    """Info (always leftmost) | copy, print, wallpaper, slideshow, delete, "...". Optional icons fold into "..."."""

    triggered = Signal(str)

    SPECS = (
        ("copy", "copy", False),
        ("print", "print", False),
        ("wallpaper", "wallpaper", False),
        ("slideshow", "slideshow", True),
        ("delete", "trash", False),
        ("more", "more", False),
    )

    def __init__(self, parent=None, height: int = 46):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setProperty("dragRegion", True)
        self._height = height
        self._info = _icon_button("info", "info", True, self, self.triggered, accent=True)
        self._buttons: dict[str, IconButton] = {
            key: _icon_button(key, icon_name, checkable, self, self.triggered) for key, icon_name, checkable in self.SPECS
        }
        self._separator = Separator(self)
        self._available = {key: True for key, _, _ in self.SPECS}
        self.arrange(set())

    def set_available(self, key: str, available: bool) -> None:
        self._available[key] = available

    def set_enabled(self, key: str, enabled: bool) -> None:
        """Grey a button out (it stays in place) instead of removing it."""
        self.button(key).setEnabled(enabled)

    def keys(self) -> list[str]:
        """Icon buttons this pill would show with nothing collapsed (Info included, first)."""
        return ["info"] + [key for key, _, _ in self.SPECS if self._available.get(key, True)]

    def button(self, key: str) -> IconButton:
        return self._info if key == "info" else self._buttons[key]

    def set_checked(self, key: str, checked: bool) -> None:
        button = self.button(key)
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)
        button.update()

    def arrange(self, hidden: set[str]) -> None:
        y = (self._height - 36) // 2
        x = geo.TOOLBAR_PAD
        self._info.move(x, y)
        x += 36 + geo.TOOLBAR_SPACING
        self._separator.setGeometry(x - 1, 0, geo.TOOLBAR_SEPARATOR, self._height)
        x += geo.TOOLBAR_SEPARATOR - 1
        shown = 1
        for key, _, _ in self.SPECS:
            button = self._buttons[key]
            visible = self._available.get(key, True) and key not in hidden
            button.setVisible(visible)
            if visible:
                button.move(x, y)
                x += 36 + geo.TOOLBAR_SPACING
                shown += 1
        self.setFixedWidth(geo.left_width(shown))


class ActionsPill(GlassPanel):
    """Rotate, favorite and Edit (Edit rightmost). Rotate / favorite fold into "..." when narrow."""

    triggered = Signal(str)

    SPECS = (("rotate", "refresh", False), ("favorite", "heart", True))

    def __init__(self, parent=None, height: int = 46):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setProperty("dragRegion", True)
        self._height = height
        self._buttons: dict[str, IconButton] = {
            key: _icon_button(key, icon_name, checkable, self, self.triggered) for key, icon_name, checkable in self.SPECS
        }
        self._edit = PillButton("Edit", "edit-image", geo.TOOLBAR_EDIT_WIDTH, self)
        self._edit.clicked.connect(lambda: self.triggered.emit("edit"))
        self._available = {key: True for key, _, _ in self.SPECS}
        self._has_edit = True
        self.arrange(set())

    def set_available(self, key: str, available: bool) -> None:
        if key == "edit":
            self._has_edit = available
        else:
            self._available[key] = available

    @property
    def has_edit(self) -> bool:
        return self._has_edit

    def set_enabled(self, key: str, enabled: bool) -> None:
        """Grey a button out (it stays in place) instead of removing it."""
        if key == "edit":
            self._edit.setEnabled(enabled)
        else:
            self.button(key).setEnabled(enabled)

    def keys(self) -> list[str]:
        return [key for key, _, _ in self.SPECS if self._available.get(key, True)]

    def button(self, key: str) -> IconButton:
        return self._buttons[key]

    def set_checked(self, key: str, checked: bool) -> None:
        button = self.button(key)
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)
        button.update()

    def has_content(self) -> bool:
        return self.width() > 0 and not self.isHidden()

    def arrange(self, hidden: set[str]) -> None:
        y = (self._height - 36) // 2
        x = geo.TOOLBAR_PAD
        shown = 0
        for key, _, _ in self.SPECS:
            button = self._buttons[key]
            visible = self._available.get(key, True) and key not in hidden
            button.setVisible(visible)
            if visible:
                button.move(x, y)
                x += 36 + geo.TOOLBAR_SPACING
                shown += 1
        if shown:
            x += geo.TOOLBAR_EDIT_GAP - geo.TOOLBAR_SPACING
        self._edit.setVisible(self._has_edit)
        if self._has_edit:
            self._edit.move(x, y)
        width = geo.right_width(shown, self._has_edit)
        self.setVisible(width > 0)
        if width > 0:
            self.setFixedWidth(width)


class WindowControls(GlassPanel):
    """Minimize, maximize / restore and close: the viewer's own caption buttons."""

    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, parent=None, height: int = 46):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setProperty("dragRegion", True)
        y = (height - 36) // 2
        x = geo.TOOLBAR_PAD
        self._min = IconButton("min", 36, 16, self)
        self._max = IconButton("max", 36, 16, self)
        self._close = IconButton("close", 36, 16, self, hover_bg=QColor("#c42b1c"))
        for button in (self._min, self._max, self._close):
            button.move(x, y)
            x += 36 + geo.TOOLBAR_SPACING
        self._min.setToolTip("Minimize")
        self._close.setToolTip("Close")
        self._min.clicked.connect(self.minimize_clicked)
        self._max.clicked.connect(self.maximize_clicked)
        self._close.clicked.connect(self.close_clicked)
        self._close.hide()  # the window draws its own, whose hit area reaches the corner (common/corner_close.py)
        self.set_maximized(False)
        self.setFixedWidth(geo.CONTROLS_WIDTH)

    def close_rect(self):
        """Where the close button belongs, in this panel's coordinates."""
        return self._close.geometry()

    def set_maximized(self, maximized: bool) -> None:
        self._max.set_icon("restore" if maximized else "max")
        self._max.setToolTip("Restore" if maximized else "Maximize")


class CounterPill(GlassPanel):
    def __init__(self, parent=None, height: int = 40):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._text = ""
        self.setFont(_font(self, 13, QFont.Weight.Medium))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance(text) + 36)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(TEXT)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


class ZoomPill(GlassPanel):
    """Zoom out - 80% - zoom in | fit - full screen (bottom right)."""

    zoom_out = Signal()
    zoom_in = Signal()
    toggle_zoom = Signal()
    fit = Signal()
    fullscreen = Signal()

    def __init__(self, parent=None, height: int = 40):
        super().__init__(parent)
        self.setFixedHeight(height)
        size = 32
        y = (height - size) // 2
        x = 5
        self._out = IconButton("zoom-out", size, 17, self)
        self._out.move(x, y)
        x += size
        self._label = PillButton("100%", None, 54, self, height=size)
        self._label.move(x, y)
        x += 54
        self._in = IconButton("zoom-in", size, 17, self)
        self._in.move(x, y)
        x += size + 4
        self._separator = Separator(self)
        self._separator.setGeometry(x, 0, 9, height)
        x += 9 + 4
        self._fit = IconButton("fit-screen", size, 17, self)
        self._fit.move(x, y)
        x += size
        self._full = IconButton("fullscreen", size, 17, self)
        self._full.move(x, y)
        x += size + 5
        self.setFixedWidth(x)
        self._out.clicked.connect(self.zoom_out)
        self._in.clicked.connect(self.zoom_in)
        self._label.clicked.connect(self.toggle_zoom)
        self._fit.clicked.connect(self.fit)
        self._full.clicked.connect(self.fullscreen)

    def set_percent(self, percent: float) -> None:
        self._label.set_label(f"{round(percent)}%")

    def set_zoom_enabled(self, enabled: bool) -> None:
        """Zooming only applies to photos; full screen always works."""
        for widget in (self._out, self._label, self._in, self._fit):
            widget.setEnabled(enabled)

    def set_fullscreen(self, on: bool) -> None:
        self._full.set_icon("restore" if on else "fullscreen")


class NavButton(IconButton):
    def __init__(self, icon_name: str, parent=None):
        super().__init__(icon_name, 44, 22, parent, solid_bg=True)


class ChromeFader(QObject):
    """Fades a set of overlay widgets in and out; faded-out ones stop taking the mouse."""

    def __init__(self, widgets: list[QWidget], parent: QObject | None = None):
        super().__init__(parent)
        self._widgets = widgets
        self._effects: list[QGraphicsOpacityEffect] = []
        self._base_transparent = [w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) for w in widgets]
        for widget in widgets:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(1.0)
            effect.setEnabled(False)  # no off-screen rendering while fully visible
            widget.setGraphicsEffect(effect)
            self._effects.append(effect)
        self._value = 1.0
        self._target = 1.0
        self._animation = make_animation(self, DURATION_NORMAL, decelerate(), 1.0, 1.0)
        self._animation.valueChanged.connect(self._apply)

    def is_shown(self) -> bool:
        return self._target > 0.5

    def fade_to(self, target: float) -> None:
        if target == self._target and self._animation.state() != QAbstractAnimation.State.Running:
            return
        self._target = target
        self._animation.stop()
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(target)
        self._animation.setDuration(DURATION_NORMAL if target > self._value else DURATION_NORMAL + DURATION_FAST)
        self._animation.setEasingCurve(decelerate() if target > self._value else accelerate())
        self._animation.start()

    def snap(self, value: float) -> None:
        self._animation.stop()
        self._target = value
        self._apply(value)

    def _apply(self, value) -> None:
        self._value = float(value)
        interactive = self._value > 0.4
        for widget, effect, base in zip(self._widgets, self._effects, self._base_transparent):
            effect.setEnabled(self._value < 0.999)
            effect.setOpacity(self._value)
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, base or not interactive)