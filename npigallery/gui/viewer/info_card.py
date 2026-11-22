"""Floating "Info" card (top left). The name field is editable in place (Enter or clicking away
renames, Esc cancels); the date fields are laid out the way their editable version will need."""

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...formatting import human_size
from ...services.scanner import MediaItem
from . import geometry as geo
from .loader import ViewData
from .overlays import IconButton
from ..theme import theme
from .style import CARD, GLASS_BORDER, accent_pair, floating

CARD_QSS = """
QLabel#title { color: #ffffff; font-size: 17px; font-weight: 700; }
QLabel#caption { color: rgba(255,255,255,150); font-size: 11px; font-weight: 500; }
QLabel#value { color: #ffffff; font-size: 13px; font-weight: 600; }
QLabel#path { color: #ffffff; font-size: 13px; }
QLabel#section { color: rgba(255,255,255,150); font-size: 11px; font-weight: 600; }
QLabel#key { color: rgba(255,255,255,150); font-size: 12px; }
QLabel#data { color: #ffffff; font-size: 12px; }
QLabel#chip { background: rgba(255,255,255,20); border-radius: 9px; color: #ffffff;
              font-size: 13px; font-weight: 500; padding: 9px 4px; }
QLineEdit#field { background: rgba(255,255,255,20); border: none; border-bottom: 2px solid #7fb2ff;
                  border-radius: 9px; color: #ffffff; font-size: 14px; padding: 9px 12px; }
QLineEdit#field:focus { background: rgba(255,255,255,32); }
QLineEdit#field:read-only { background: rgba(255,255,255,20); }
QFrame#rule { background: rgba(255,255,255,40); border: none; min-height: 1px; max-height: 1px; }
QScrollArea, QWidget#infoContent { background: transparent; border: none; }
"""


def _breakable_key(key: str) -> str:
    """EXIF names are CamelCase ("ShutterSpeedValue"): allow a line break between the words."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", "\u200b", key)


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


class NameField(QLineEdit):
    """The file name (without extension). Clicking puts the caret in it; every key typed while
    it is being edited stays here, so arrows / Backspace never navigate the photos."""

    committed = Signal(str)  # the new name, without extension
    finished = Signal()  # Enter / Esc: the window should take the keyboard back

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("field")
        self._original = ""
        self._editing = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.IBeamCursor)

    def set_original(self, text: str) -> None:
        self._original = text
        if not self._editing:  # never pull the text from under the user's fingers
            self.setText(text)
            self.setCursorPosition(0)

    def set_renamable(self, renamable: bool) -> None:
        self.setReadOnly(not renamable)
        self.setCursor(Qt.CursorShape.IBeamCursor if renamable else Qt.CursorShape.ArrowCursor)

    def is_editing(self) -> bool:
        return self._editing

    def begin_edit(self, select_all: bool = True) -> None:
        if self.isReadOnly():
            return
        self._editing = True
        self.setFocus(Qt.FocusReason.ShortcutFocusReason if select_all else Qt.FocusReason.MouseFocusReason)
        if select_all:
            QTimer.singleShot(0, self.selectAll)

    def commit(self) -> None:
        if self._editing:
            self._finish(True)

    def forward_key(self, event) -> None:
        self.keyPressEvent(event)

    def _finish(self, commit: bool) -> None:
        text = self.text().strip()
        self._editing = False
        self.setText(self._original)
        self.setCursorPosition(0)
        self.deselect()
        if commit and text and text != self._original:
            self.committed.emit(text)

    def mousePressEvent(self, event) -> None:
        if not self.isReadOnly() and not self._editing:
            self.begin_edit(select_all=False)  # the caret goes where the click is
        super().mousePressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        if not self.isReadOnly():
            self._editing = True

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self._editing and event.reason() != Qt.FocusReason.PopupFocusReason:
            self._finish(True)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if self._editing and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish(True)
            self.finished.emit()
        elif self._editing and key == Qt.Key.Key_Escape:
            self._finish(False)
            self.finished.emit()
        else:
            super().keyPressEvent(event)
        event.accept()


class InfoCard(QWidget):
    close_requested = Signal()
    rename_requested = Signal(str)  # new name without extension
    edit_finished = Signal()

    WIDTH = 288
    PAD = 18
    HEADER = 14 + 30 + 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        theme.signals().changed.connect(self.update)  # repaint when the floating opacity changes
        self.setStyleSheet(CARD_QSS.replace("#7fb2ff", accent_pair()[0].name()))
        self._path = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(self.PAD, 14, self.PAD, self.PAD)
        root.setSpacing(0)
        header = QHBoxLayout()
        header.addWidget(_label("Info", "title"), 1)
        close = IconButton("close", 30, 14, self)
        close.clicked.connect(self.close_requested)
        header.addWidget(close)
        root.addLayout(header)
        root.addSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrow keys keep navigating photos
        self._content = QWidget()
        self._content.setObjectName("infoContent")
        self._content.setFixedWidth(self.WIDTH - 2 * self.PAD)
        body = QVBoxLayout(self._content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

        body.addWidget(_label("NAME", "caption"))
        body.addSpacing(6)
        self._name = NameField()
        self._name.committed.connect(self.rename_requested)
        self._name.finished.connect(self.edit_finished)
        self._name.set_renamable(False)
        body.addWidget(self._name)
        body.addSpacing(16)

        body.addWidget(_label("DATE TAKEN", "caption"))
        body.addSpacing(6)
        grid = QGridLayout()
        grid.setSpacing(6)
        self._chips: list[QLabel] = []
        for index in range(6):
            chip = _label("", "chip")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(chip, index // 3, index % 3)
            self._chips.append(chip)
        body.addLayout(grid)
        body.addSpacing(16)
        rule = QFrame()
        rule.setObjectName("rule")
        body.addWidget(rule)
        body.addSpacing(16)

        details = QGridLayout()
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(14)
        self._values: dict[str, QLabel] = {}
        for index, key in enumerate(("Dimensions", "File size", "Resolution", "Bit depth", "Source")):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(_label(key, "caption"))
            value = _label("-", "value", wrap=True)
            cell.addWidget(value)
            self._values[key] = value
            row, column = divmod(index, 2)
            details.addLayout(cell, row, column, 1, 2 if key == "Source" else 1)
        body.addLayout(details)
        body.addSpacing(14)

        body.addWidget(_label("File path", "caption"))
        body.addSpacing(2)
        path_row = QHBoxLayout()
        self._path_label = _label("", "path", wrap=True)
        path_row.addWidget(self._path_label, 1)
        copy = IconButton("copy", 30, 15, self)
        copy.clicked.connect(self._copy_path)
        path_row.addWidget(copy, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(path_row)

        self._extra = QVBoxLayout()
        self._extra.setSpacing(0)
        body.addLayout(self._extra)
        body.addStretch(1)

    # // name editing
    def set_renamable(self, renamable: bool) -> None:
        self._name.set_renamable(renamable)

    def is_editing(self) -> bool:
        return self._name.is_editing()

    def focus_name(self) -> None:
        self._name.begin_edit(select_all=True)

    def commit_edit(self) -> None:
        self._name.commit()

    def forward_key(self, event) -> None:
        self._name.forward_key(event)

    # // content
    def _copy_path(self) -> None:
        QApplication.clipboard().setText(self._path)

    def _clear_extra(self) -> None:
        while self._extra.count():
            entry = self._extra.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.deleteLater()

    def populate(self, item: MediaItem, data: ViewData | None) -> None:
        self._path = str(item.path)
        self._name.set_original(Path(item.name).stem)
        stamp = (data.taken_at if data and data.taken_at else None) or item.sort_time
        parts = geo.time_parts(stamp) or ("-",) * 6
        for chip, text in zip(self._chips, parts):
            chip.setText(text)
        width = data.width if data else item.width
        height = data.height if data else item.height
        size = data.file_size if data else item.file_size or item.size
        self._values["Dimensions"].setText(f"{width} × {height}" if width and height else "-")
        self._values["File size"].setText(human_size(size) if size else "-")
        self._values["Resolution"].setText(data.dpi if data else "-")
        self._values["Bit depth"].setText(data.depth if data else "-")
        self._values["Source"].setText("NPI container" if item.is_npi else "This PC")
        self._path_label.setText(geo.breakable(self._path))
        self._clear_extra()
        if data is not None:
            for title, rows in data.sections:
                if rows:
                    self._add_section(title, rows)
        self._content.layout().activate()

    def _add_section(self, title: str, rows: list[tuple[str, str]]) -> None:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 18, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_label(title.upper(), "section"))
        for key, value in rows:
            line = QHBoxLayout()
            line.setSpacing(10)
            left = _label(_breakable_key(key), "key", wrap=True)
            left.setFixedWidth(96)
            right = _label(geo.breakable(value), "data", wrap=True)
            line.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)
            line.addWidget(right, 1)
            layout.addLayout(line)
        self._extra.addWidget(holder)

    def desired_height(self) -> int:
        self._content.layout().activate()
        self._content.adjustSize()
        return self.HEADER + self._content.height() + self.PAD

    # // painting
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(GLASS_BORDER, 1))
        painter.setBrush(floating(CARD))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 18, 18)