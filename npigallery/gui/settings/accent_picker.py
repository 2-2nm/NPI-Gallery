from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIntValidator, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..theme import accent, theme
from ..common.dialogs import FloatDialog, button_row, dialog_label, make_button, make_input
from ..common.icons import draw_icon, icon
from ..common.motion import DURATION_SLOW, FrameAnimation, accelerate, decelerate
from ..theme.preferences import prefs

SWATCH_SIZE = 40
SWATCH_GAP = 8
GRID_COLUMNS = 8
SQUARE_HEIGHT = 190
BAR_HEIGHT = 16
PAIR_GAP = 14
PAIR_SLIDE = 56
SCREEN_EDGE = 8


def _tone(role: str) -> QColor:
    return QColor(getattr(theme.palette(), role))


def _shown_tone(color: str) -> str:
    palette = theme.palette()
    return accent.readable_on(color, palette.float_bg, palette.dark)


class Swatch(QAbstractButton):
    def __init__(self, color: str, name: str = "", parent=None):
        super().__init__(parent)
        self._color = color
        self._selected = False
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)

    def color(self) -> str:
        return self._color

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._selected:
            painter.setPen(QPen(_tone("text"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(outer, 8, 8)
            inner = outer.adjusted(4, 4, -4, -4)
        else:
            inner = outer.adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawRoundedRect(inner, 5, 5)
        if self._selected:
            draw_icon(painter, "check", inner.adjusted(9, 9, -9, -9), QColor(accent.on_color(self._color)), stroke=3.0)
        elif self.underMouse():
            painter.setPen(QPen(_tone("text_muted"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(outer, 8, 8)


class AddSwatch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        active = self.underMouse()
        painter.setPen(QPen(_tone("stroke_hover" if active else "stroke"), 1.5))
        painter.setBrush(_tone("control_hover") if active else _tone("control"))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QPen(_tone("text"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        centre = rect.center()
        painter.drawLine(QPointF(centre.x() - 6, centre.y()), QPointF(centre.x() + 6, centre.y()))
        painter.drawLine(QPointF(centre.x(), centre.y() - 6), QPointF(centre.x(), centre.y() + 6))


class ColorDot(QWidget):
    def __init__(self, size: int = 20, parent=None):
        super().__init__(parent)
        self._color = "#0078d4"
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_color(self, color: str) -> None:
        self._color = accent.normalize(color) or self._color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        painter.setPen(QPen(_tone("stroke_hover"), 1.2))
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(rect)


class PreviewChip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = "#0078d4"
        self.setFixedHeight(40)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        tone = _shown_tone(self._color)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tone))
        painter.drawRoundedRect(rect, 14, 14)
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(accent.on_color(tone)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Preview")


class ColorSquare(QWidget):
    changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self._saturation = 1.0
        self.setFixedHeight(SQUARE_HEIGHT)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_state(self, hue: float, saturation: float) -> None:
        self._hue, self._saturation = hue, saturation
        self.update()

    def _pick(self, position) -> None:
        width = max(1.0, self.width() - 1.0)
        height = max(1.0, self.height() - 1.0)
        self._hue = min(1.0, max(0.0, position.x() / width))
        self._saturation = 1.0 - min(1.0, max(0.0, position.y() / height))
        self.update()
        self.changed.emit(self._hue, self._saturation)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.setClipPath(path)
        horizontal = QLinearGradient(rect.topLeft(), rect.topRight())
        for step in range(7):
            horizontal.setColorAt(step / 6, QColor.fromHsvF(min(step / 6, 1.0), 1.0, 1.0))
        painter.fillRect(rect, horizontal)
        vertical = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        vertical.setColorAt(0.0, QColor(255, 255, 255, 0))
        vertical.setColorAt(1.0, QColor(255, 255, 255, 255))
        painter.fillRect(rect, vertical)
        painter.setClipping(False)
        centre = QPointF(rect.left() + self._hue * rect.width(), rect.top() + (1.0 - self._saturation) * rect.height())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 3.5))
        painter.drawEllipse(centre, 7, 7)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(centre, 7, 7)


class BrightnessSlider(QWidget):
    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self._saturation = 1.0
        self._value = 1.0
        self.setFixedHeight(BAR_HEIGHT + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, hue: float, saturation: float, value: float) -> None:
        self._hue, self._saturation, self._value = hue, saturation, value
        self.update()

    def _track(self) -> QRectF:
        return QRectF(8, 4, max(1.0, self.width() - 16.0), BAR_HEIGHT)

    def _pick(self, position) -> None:
        track = self._track()
        self._value = min(1.0, max(0.0, (position.x() - track.left()) / track.width()))
        self.update()
        self.changed.emit(self._value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(event.position())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        gradient = QLinearGradient(track.topLeft(), track.topRight())
        gradient.setColorAt(0.0, QColor(0, 0, 0))
        gradient.setColorAt(1.0, QColor(accent.from_hsv(self._hue, self._saturation, 1.0)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(track, BAR_HEIGHT / 2, BAR_HEIGHT / 2)
        centre = QPointF(track.left() + self._value * track.width(), track.center().y())
        painter.setBrush(QColor(accent.from_hsv(self._hue, self._saturation, self._value)))
        painter.setPen(QPen(QColor(0, 0, 0, 150), 3.5))
        painter.drawEllipse(centre, 8, 8)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(centre, 8, 8)


class ChannelField(QWidget):
    edited = Signal()

    def __init__(self, label: str, maximum: int, parent=None):
        super().__init__(parent)
        self._maximum = maximum
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(dialog_label(label, "detail"))
        self.edit = make_input("0")
        self.edit.setValidator(QIntValidator(0, maximum, self.edit))
        self.edit.setMaxLength(len(str(maximum)))
        self.edit.textEdited.connect(lambda _text: self.edited.emit())
        layout.addWidget(self.edit)

    def value(self) -> int | None:
        text = self.edit.text()
        if text.isdigit() and int(text) <= self._maximum:
            return int(text)
        return None

    def set_value(self, value: int) -> None:
        if self.edit.text() != str(value):
            self.edit.setText(str(value))


def _group(title: str, content) -> QVBoxLayout:
    group = QVBoxLayout()
    group.setContentsMargins(0, 0, 0, 0)
    group.setSpacing(8)
    group.addWidget(dialog_label(title))
    if isinstance(content, QWidget):
        group.addWidget(content)
    else:
        group.addLayout(content)
    return group


def _field_row(fields: list[ChannelField]) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    for field in fields:
        row.addWidget(field, 1)
    return row


class CustomColorDialog(FloatDialog):
    def __init__(self, initial: str, parent=None):
        super().__init__("Choose a custom accent color", parent, 456)
        self._anchor = parent if isinstance(parent, FloatDialog) else None
        self._plan: tuple[QPoint, QPoint, QPoint] | None = None
        self._animation: FrameAnimation | None = None
        self._entered = False
        self._closing = False
        start = accent.normalize(initial) or accent.PRESETS[8][1]
        self._pending = start
        self._hsv = accent.to_hsv(start)

        self._square = ColorSquare()
        self._square.changed.connect(self._on_square)
        self.body.addWidget(self._square)
        self._slider = BrightnessSlider()
        self._slider.changed.connect(self._on_slider)
        self.body.addWidget(self._slider)

        self._more = QPushButton("More")
        self._more.setObjectName("Tool")
        self._more.setCheckable(True)
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setIcon(icon("chevron-down", None, 14))
        self._more.setIconSize(QSize(14, 14))
        self._more.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._more.toggled.connect(self._toggle_more)
        more_row = QHBoxLayout()
        more_row.setContentsMargins(0, 0, 0, 0)
        more_row.addStretch(1)
        more_row.addWidget(self._more)
        self.body.addLayout(more_row)

        self._fields = QWidget()
        fields = QVBoxLayout(self._fields)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(10)
        self._rgb = [ChannelField(label, 255) for label in ("Red", "Green", "Blue")]
        self._hsv_fields = [ChannelField("Hue", 359), ChannelField("Saturation", 100), ChannelField("Value", 100)]
        for field in self._rgb:
            field.edited.connect(self._on_rgb)
        for field in self._hsv_fields:
            field.edited.connect(self._on_hsv)
        fields.addLayout(_field_row(self._rgb))
        fields.addLayout(_field_row(self._hsv_fields))
        hex_row = QHBoxLayout()
        hex_row.setContentsMargins(0, 0, 0, 0)
        hex_column = QVBoxLayout()
        hex_column.setContentsMargins(0, 0, 0, 0)
        hex_column.setSpacing(4)
        hex_column.addWidget(dialog_label("Hex", "detail"))
        self._hex = make_input(start)
        self._hex.setFixedWidth(130)
        self._hex.setMaxLength(7)
        self._hex.textEdited.connect(self._on_hex)
        hex_column.addWidget(self._hex)
        hex_row.addLayout(hex_column)
        hex_row.addStretch(1)
        fields.addLayout(hex_row)
        self._fields.setVisible(False)
        self.body.addWidget(self._fields)

        self._preview = PreviewChip()
        self.body.addLayout(_group("Color preview", self._preview))

        cancel = make_button("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        done = make_button("Done", "primary")
        done.clicked.connect(self.accept)
        self.body.addSpacing(2)
        self.body.addLayout(button_row(cancel, done))
        self._sync_widgets("init")
        self.finish()
        self._plan_pair()

    def pending(self) -> str:
        return self._pending

    def _plan_pair(self) -> None:
        anchor = self._anchor
        if anchor is None:
            return
        margin = self.MARGIN
        anchor_card = anchor.geometry().adjusted(margin, margin, -margin, -margin)
        own_width = self.width() - 2 * margin
        total = anchor_card.width() + PAIR_GAP + own_width
        centre = anchor_card.center()
        screen = anchor.screen().availableGeometry()
        left = centre.x() - total // 2
        left = max(screen.left() + SCREEN_EDGE, min(left, screen.right() - SCREEN_EDGE - total))
        anchor_target = QPoint(left - margin, centre.y() - anchor.height() // 2)
        own_target = QPoint(left + anchor_card.width() + PAIR_GAP - margin, centre.y() - self.height() // 2)
        self._plan = (anchor.pos(), anchor_target, own_target)
        self.move(own_target + QPoint(PAIR_SLIDE, 0))
        self.setWindowOpacity(0.0)

    def _apply_progress(self, progress: float) -> None:
        if self._plan is None or self._anchor is None:
            return
        origin, anchor_target, own_target = self._plan
        self._anchor.move(origin + (anchor_target - origin) * progress)
        self.move(own_target + QPoint(round(PAIR_SLIDE * (1.0 - progress)), 0))
        self.setWindowOpacity(min(1.0, max(0.0, progress)))

    def _run(self, start: float, end: float, curve, on_finished=None) -> None:
        if self._animation is not None:
            self._animation.stop()
        animation = FrameAnimation(self, DURATION_SLOW, curve, start, end)
        animation.valueChanged.connect(self._apply_progress)
        if on_finished is not None:
            animation.finished.connect(on_finished)
        self._animation = animation
        animation.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._plan is not None and not self._entered:
            self._entered = True
            self._run(0.0, 1.0, decelerate())

    def done(self, result: int) -> None:
        if self._plan is None or self._closing or not self.isVisible():
            super().done(result)
            return
        self._closing = True
        self._run(1.0, 0.0, accelerate(), lambda: self._leave(result))

    def _leave(self, result: int) -> None:
        self._apply_progress(0.0)
        super().done(result)

    def _toggle_more(self, shown: bool) -> None:
        self._more.setIcon(icon("chevron-up" if shown else "chevron-down", None, 14))
        self._fields.setVisible(shown)
        self.refit()

    def _sync_widgets(self, source: str) -> None:
        self._preview.set_color(self._pending)
        if source not in ("square", "slider", "hsv"):
            hue, saturation, value = accent.to_hsv(self._pending)
            if value < 0.02:
                hue, saturation = self._hsv[0], self._hsv[1]
            elif saturation < 0.02:
                hue = self._hsv[0]
            self._hsv = (hue, saturation, value)
        hue, saturation, value = self._hsv
        self._square.set_state(hue, saturation)
        self._slider.set_state(hue, saturation, value)
        if source != "hex":
            self._hex.setText(self._pending)
        if source != "rgb":
            for field, channel in zip(self._rgb, accent.to_rgb(self._pending)):
                field.set_value(channel)
        if source != "hsv":
            numbers = (round(hue * 360) % 360, round(saturation * 100), round(value * 100))
            for field, number in zip(self._hsv_fields, numbers):
                field.set_value(number)

    def set_pending(self, color: str, source: str = "init") -> None:
        value = accent.normalize(color)
        if value is None:
            return
        self._pending = value
        self._sync_widgets(source)

    def _on_square(self, hue: float, saturation: float) -> None:
        self._hsv = (hue, saturation, self._hsv[2])
        self.set_pending(accent.from_hsv(*self._hsv), "square")

    def _on_slider(self, value: float) -> None:
        self._hsv = (self._hsv[0], self._hsv[1], value)
        self.set_pending(accent.from_hsv(*self._hsv), "slider")

    def _on_hex(self, text: str) -> None:
        self.set_pending(text, "hex")

    def _on_rgb(self) -> None:
        current = accent.to_rgb(self._pending)
        values = [current[index] if field.value() is None else field.value() for index, field in enumerate(self._rgb)]
        self.set_pending(accent.from_rgb(*values), "rgb")

    def _on_hsv(self) -> None:
        hue, saturation, value = self._hsv
        h, s, v = (field.value() for field in self._hsv_fields)
        self._hsv = (
            hue if h is None else h / 360,
            saturation if s is None else s / 100,
            value if v is None else v / 100,
        )
        self.set_pending(accent.from_hsv(*self._hsv), "hsv")


def choose_custom_color(parent, initial: str) -> str | None:
    dialog = CustomColorDialog(initial, parent)
    accepted = dialog.exec() == CustomColorDialog.DialogCode.Accepted
    result = dialog.pending() if accepted else None
    dialog.deleteLater()
    return result


class AccentDialog(FloatDialog):
    def __init__(self, parent=None):
        super().__init__("Choose accent color", parent, 456)
        self._swatches: list[Swatch] = []
        self._custom_swatches: list[Swatch] = []
        initial = accent.normalize(prefs().get("accent_color")) or accent.PRESETS[8][1]
        self._pending = initial

        history = accent.recent()
        if history:
            row = self._row(history)
            self.body.addLayout(_group("Recent colors", row))

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SWATCH_GAP)
        for position, (name, value) in enumerate(accent.PRESETS):
            grid.addWidget(self._swatch(value, name), position // GRID_COLUMNS, position % GRID_COLUMNS)
        grid.setColumnStretch(GRID_COLUMNS, 1)
        self.body.addLayout(_group("Colors", grid))

        self._custom_row = QHBoxLayout()
        self._custom_row.setContentsMargins(0, 0, 0, 0)
        self._custom_row.setSpacing(SWATCH_GAP)
        for value in accent.custom():
            swatch = self._swatch(value, value)
            self._custom_swatches.append(swatch)
            self._custom_row.addWidget(swatch)
        self._add = AddSwatch()
        self._add.clicked.connect(self._open_custom)
        self._custom_row.addWidget(self._add)
        self._custom_row.addStretch(1)
        self.body.addLayout(_group("Custom colors", self._custom_row))

        self._preview = PreviewChip()
        self.body.addLayout(_group("Color preview", self._preview))

        cancel = make_button("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        done = make_button("Done", "primary")
        done.clicked.connect(self.commit)
        self.body.addSpacing(2)
        self.body.addLayout(button_row(cancel, done))
        self._sync_widgets()
        self.finish()

    def pending(self) -> str:
        return self._pending

    def _swatch(self, value: str, name: str) -> Swatch:
        swatch = Swatch(value, name)
        swatch.clicked.connect(lambda _checked=False, value=value: self.set_pending(value))
        self._swatches.append(swatch)
        return swatch

    def _row(self, colors: list[str]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SWATCH_GAP)
        for value in colors:
            row.addWidget(self._swatch(value, value))
        row.addStretch(1)
        return row

    def _sync_widgets(self) -> None:
        for swatch in self._swatches:
            swatch.set_selected(swatch.color() == self._pending)
        self._preview.set_color(self._pending)

    def set_pending(self, color: str) -> None:
        value = accent.normalize(color)
        if value is None:
            return
        self._pending = value
        self._sync_widgets()

    def _open_custom(self) -> None:
        result = choose_custom_color(self, self._pending)
        if result is not None:
            self.adopt_custom(result)

    def adopt_custom(self, color: str) -> None:
        value = accent.normalize(color)
        if value is None:
            return
        if all(swatch.color() != value for swatch in self._swatches):
            swatch = self._swatch(value, value)
            self._custom_swatches.insert(0, swatch)
            self._custom_row.insertWidget(0, swatch)
            while len(self._custom_swatches) > accent.CUSTOM_LIMIT:
                stale = self._custom_swatches.pop()
                self._swatches.remove(stale)
                self._custom_row.removeWidget(stale)
                stale.deleteLater()
        self.set_pending(value)
        self.refit()

    def commit(self) -> None:
        accent.choose(self._pending)
        self.accept()


def open_accent_dialog(parent) -> None:
    dialog = AccentDialog(parent)
    dialog.exec()
    dialog.deleteLater()
