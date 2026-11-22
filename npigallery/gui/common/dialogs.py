"""Floating, frameless dialogs that match the rounded "pill" look of the main window.

Everything that used to be a native QMessageBox / QInputDialog (and the custom Batch and Share
dialogs) is built from `FloatDialog`. Folder pickers (QFileDialog) deliberately stay native.

Public helpers:
    inform(parent, title, text)                       - one OK button
    warn(parent, title, text)                         - one OK button
    confirm(parent, title, text, ok_text=, danger=)   - Cancel / OK, returns bool
    ask_text(parent, title, label, text="")           - returns the entered text or None
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import theme
from .icons import draw_icon, icon, themed_pixmap

BUTTON_HEIGHT = 36
ACTION_HEIGHT = 42
_BUTTON_NAMES = {
    "secondary": "DialogButton",
    "primary": "DialogPrimary",
    "danger": "DialogDanger",
    "action": "DialogAction",
}
_LABEL_NAMES = {
    "label": "DialogLabel",
    "text": "DialogText",
    "name": "DialogName",
    "detail": "DialogDetail",
    "danger": "DialogDanger",
}


# // small building blocks
def dialog_label(text: str = "", kind: str = "label", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(_LABEL_NAMES[kind])
    label.setWordWrap(wrap)
    return label


def make_button(text: str, kind: str = "secondary", icon_name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(_BUTTON_NAMES[kind])
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedHeight(ACTION_HEIGHT if kind == "action" else BUTTON_HEIGHT)
    primary = kind in ("primary", "danger")
    button.setAutoDefault(primary)
    button.setDefault(primary)
    if icon_name:
        button.setIcon(icon(icon_name, None, 17))
        button.setIconSize(QSize(17, 17))
    return button


def make_input(text: str = "") -> QLineEdit:
    edit = QLineEdit(text)
    edit.setObjectName("DialogInput")
    edit.setFixedHeight(BUTTON_HEIGHT)
    return edit


def button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addStretch(1)
    for button in buttons:
        row.addWidget(button)
    return row


class DialogCheck(QCheckBox):
    """Rounded checkbox drawn by hand"""

    BOX = 16
    GAP = 10

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(self.BOX + self.GAP + metrics.horizontalAdvance(self.text()) + 2, max(24, metrics.height() + 6))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        p = theme.palette()
        enabled = self.isEnabled()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = (self.height() - self.BOX) / 2
        box = QRectF(0.5, top + 0.5, self.BOX - 1, self.BOX - 1)
        if not enabled:
            fill = QColor(p.default_disabled_bg)
        elif self.underMouse():
            fill = QColor(p.default_hover_bg)
        else:
            fill = QColor(p.default_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4.5, 4.5)
        if self.isChecked():
            tick = QColor(p.default_fg if enabled else p.default_disabled_fg)
            draw_icon(painter, "check", box.adjusted(2.2, 2.2, -2.2, -2.2), tick, stroke=3.2)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p.text_muted), 1.5))
            painter.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 6.5, 6.5)
        painter.setPen(QColor(p.text if enabled else p.disabled_text))
        text_rect = QRectF(self.BOX + self.GAP, 0, self.width() - self.BOX - self.GAP, self.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class ElidedLabel(QLabel):
    """Single-line label that shortens its text in the middle instead of widening the dialog."""

    def __init__(self, text: str, object_name: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName(object_name)
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 10))
        if shown != self.text():
            QLabel.setText(self, shown)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()


class RoundedThumb(QWidget):
    """Square preview with rounded corners; the photo is cropped to fill it."""

    def __init__(self, size: int = 58, radius: int = 12, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._pixmap: QPixmap | None = None
        self.setFixedSize(size, size)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self.update()

    def paintEvent(self, event) -> None:
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.save()
        painter.setClipPath(path)
        painter.fillPath(path, QColor(p.thumb_bg))
        if self._pixmap is not None:
            ratio = self.devicePixelRatioF()
            target = self.size() * ratio
            scaled = self._pixmap.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(ratio)
            x = (self.width() - scaled.width() / ratio) / 2
            y = (self.height() - scaled.height() / ratio) / 2
            painter.drawPixmap(QPointF(x, y), scaled)
        else:
            glyph = themed_pixmap("gallery", "icon_muted", 24)
            gx = (self.width() - glyph.width() / glyph.devicePixelRatio()) / 2
            gy = (self.height() - glyph.height() / glyph.devicePixelRatio()) / 2
            painter.drawPixmap(QPointF(gx, gy), glyph)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(p.stroke), 1))
        painter.drawRoundedRect(rect, self._radius, self._radius)


# // the dialog shell
class FloatDialog(QDialog):
    """Frameless rounded card with a title, a close button and a soft shadow.

    Subclasses add content to `self.body` (a QVBoxLayout) and call `self.finish()` once, and
    `self.refit()` again whenever the content changes height (a row shown or hidden).
    """

    MARGIN = 20  # transparent room around the card for the shadow
    HEADER_HEIGHT = 56

    def __init__(self, title: str, parent=None, width: int = 440):
        super().__init__(parent)
        self._card_width = width
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(title)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        outer.setSpacing(0)
        self._card = QFrame(self)
        self._card.setObjectName("DialogCard")
        self._card.setFixedWidth(width)
        outer.addWidget(self._card)

        self.body = QVBoxLayout(self._card)
        self.body.setContentsMargins(20, 20, 20, 20)
        self.body.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = QLabel(title)
        self._title.setObjectName("DialogTitle")
        self._close_button = QToolButton()
        self._close_button.setObjectName("DialogClose")
        self._close_button.setFixedSize(32, 32)
        self._close_button.setIcon(icon("close", None, 16))
        self._close_button.setIconSize(QSize(16, 16))
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.clicked.connect(self.reject)
        header.addWidget(self._title, 1)
        header.addWidget(self._close_button)
        self.body.addLayout(header)

    # // sizing
    def refit(self) -> None:
        layout = self.body
        layout.activate()
        inner = self._card_width - 2  # the card has a 1px border on each side
        height = layout.heightForWidth(inner)
        if height < 0:
            height = layout.sizeHint().height()
        height = max(height, layout.minimumSize().height()) + 2
        self.setFixedSize(self._card_width + 2 * self.MARGIN, height + 2 * self.MARGIN)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refit()  # children are polished by now, so fonts from the stylesheet are final

    def finish(self) -> None:
        self.refit()

    # // painting / dragging
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)
        peak = 12 if theme.palette().dark else 5
        painter.setPen(Qt.PenStyle.NoPen)
        reach = self.MARGIN - 4
        for spread in range(1, reach):
            alpha = int(peak * (1 - spread / reach) ** 2)
            if alpha <= 0:
                continue
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                card.adjusted(-spread, -spread + 4, spread, spread + 4), 22 + spread, 22 + spread
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < self.MARGIN + self.HEADER_HEIGHT:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


# /// message / confirm / text prompt
_MAX_TEXT_HEIGHT = 260


def _text_block(text: str, content_width: int) -> QWidget:
    label = dialog_label(text, "text", wrap=True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    needed = QFontMetrics(label.font()).boundingRect(
        0, 0, content_width, 100000, Qt.TextFlag.TextWordWrap.value, text
    ).height()
    if needed <= _MAX_TEXT_HEIGHT:
        return label
    scroll = QScrollArea()
    scroll.setObjectName("DialogScroll")
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(False)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    label.setFixedWidth(content_width - 14)
    label.setFixedHeight(needed + 8)
    scroll.setWidget(label)
    scroll.setFixedHeight(_MAX_TEXT_HEIGHT)
    return scroll


class MessageDialog(FloatDialog):
    def __init__(
        self,
        title: str,
        text: str,
        parent=None,
        *,
        ok_text: str = "OK",
        cancel_text: str | None = None,
        danger: bool = False,
        width: int = 420,
    ):
        super().__init__(title, parent, width)
        self.body.addWidget(_text_block(text, width - 40))
        ok = make_button(ok_text, "danger" if danger else "primary")
        ok.clicked.connect(self.accept)
        buttons: list[QPushButton] = []
        if cancel_text:
            cancel = make_button(cancel_text, "secondary")
            cancel.clicked.connect(self.reject)
            buttons.append(cancel)
            if danger:  # Enter should never trigger a destructive action by accident
                ok.setAutoDefault(False)
                ok.setDefault(False)
                cancel.setAutoDefault(True)
                cancel.setDefault(True)
        buttons.append(ok)
        self.body.addSpacing(4)
        self.body.addLayout(button_row(*buttons))
        self.finish()

# i left the body there

class TextPromptDialog(FloatDialog):
    def __init__(self, title: str, label: str, text: str = "", parent=None, *, ok_text: str = "OK", width: int = 420):
        super().__init__(title, parent, width)
        group = QVBoxLayout()
        group.setSpacing(6)
        group.addWidget(dialog_label(label))
        self.edit = make_input(text)
        group.addWidget(self.edit)
        self.body.addLayout(group)
        cancel = make_button("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        ok = make_button(ok_text, "primary")
        ok.clicked.connect(self.accept)
        self.body.addSpacing(4)
        self.body.addLayout(button_row(cancel, ok))
        self.finish()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.edit.setFocus()
        self.edit.selectAll()


def _run(dialog: QDialog) -> bool:
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return accepted


def inform(parent, title: str, text: str) -> None:
    dialog = MessageDialog(title, text, parent)
    _run(dialog)
    dialog.deleteLater()


def warn(parent, title: str, text: str) -> None:
    dialog = MessageDialog(title, text, parent)
    _run(dialog)
    dialog.deleteLater()


def confirm(
    parent, title: str, text: str, ok_text: str = "Yes", cancel_text: str = "Cancel", danger: bool = False
) -> bool:
    dialog = MessageDialog(title, text, parent, ok_text=ok_text, cancel_text=cancel_text, danger=danger)
    accepted = _run(dialog)
    dialog.deleteLater()
    return accepted


def ask_text(parent, title: str, label: str, text: str = "", ok_text: str = "OK") -> str | None:
    dialog = TextPromptDialog(title, label, text, parent, ok_text=ok_text)
    accepted = _run(dialog)
    value = dialog.edit.text()
    dialog.deleteLater()
    return value if accepted else None
