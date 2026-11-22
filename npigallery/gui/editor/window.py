"""The editor overlay: it covers the photo viewer while a photo is being edited.

This file is the shell around the tools: top row (undo / redo / reset, the tool tabs, Save options /
Cancel), the undo history, loading and saving. Each tool has its own picture and controls, in its own files:

    Crop        canvas.py (picture with the frame) + panel.py (straighten dial, rotate / flip, shapes)
    Adjustment  adjust_view.py (picture) + adjust_panel.py (Light / Color sliders); pixels in services/adjust.py
    Filter      adjust_view.py (picture) + filter_panel.py (looks, Intensity); pixels in services/filters.py

The tools share one `EditState`, so undo / redo / reset cover all of them. Nothing is written until the
person saves; Cancel just closes the overlay.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...services import editing as ed
from ..common.corner_close import CornerClose
from ..common.dialogs import confirm, warn
from ..common.icons import draw_icon
from ..common.motion import AnimatedMenu
from ..viewer import style as vs
from .adjust_panel import AdjustPanel
from .adjust_view import AdjustView
from .canvas import EditCanvas
from .filter_panel import FilterPanel
from .header import BUTTON, HeaderBar
from .panel import BottomBar, GlyphButton, ratio_name

HISTORY_LIMIT = 100
LOOK_SIDE = 320  # the photo the Filter tool's thumbnails are made from is at most this many pixels on its long side
MODES = ("Crop", "Adjustment", "Filter")  # the tools that work; each has its own picture and controls (see `_set_mode`)
# (name, icon): the tools are shown as icons, the name is the tooltip
TABS = (
    ("Crop", "crop"),
    ("Adjustment", "adjust"),
    ("Filter", "filters"),
    ("Markup", "markup"),
    ("Erase", "erase"),
    ("Background", "background"),
)

# save(item, state, copy, parent window) -> the item as it is after saving, or None when saving failed
SaveHook = Callable[..., object]


def _style() -> str:
    primary, on_primary = vs.accent_pair()
    return f"""
#EditorView {{ background: #1c1c1c; }}
QLabel#EdMessage {{ color: rgba(255,255,255,0.7); font-size: 15px; background: transparent; }}
QPushButton#EdBtn {{ background: rgba(255,255,255,0.09); color: #f2f2f2; border: 1px solid rgba(255,255,255,0.12);
    border-radius: 7px; padding: 0 16px; min-height: 32px; font-size: 13px; }}
QPushButton#EdBtn:hover {{ background: rgba(255,255,255,0.15); }}
QPushButton#EdBtn:disabled {{ color: rgba(255,255,255,0.35); background: rgba(255,255,255,0.04); }}
QPushButton#EdBtn::menu-indicator {{ subcontrol-position: right center; subcontrol-origin: padding; right: 8px; }}
QPushButton#EdPrimary {{ background: {primary.name()}; color: {on_primary.name()}; border: none;
    border-radius: 7px; padding: 0 22px; min-height: 32px; font-size: 13px; font-weight: 600; }}
QPushButton#EdPrimary:hover {{ background: {primary.lighter(112).name()}; }}
QPushButton#EdPrimary:disabled {{ background: rgba(255,255,255,0.10); color: rgba(255,255,255,0.35); }}
QPushButton#EdFlat {{ background: transparent; color: #f2f2f2; border: none; border-radius: 7px;
    padding: 0 12px; min-height: 32px; font-size: 13px; }}
QPushButton#EdFlat:hover {{ background: rgba(255,255,255,0.10); }}
QPushButton#EdFlat:disabled {{ color: rgba(255,255,255,0.35); }}
"""


class TabButton(QToolButton):
    """A tool tab drawn as an icon; the active tool has a short white bar under it."""

    def __init__(self, icon_name: str, tip: str, active: bool, available: bool, parent=None):
        super().__init__(parent)
        self._icon, self._active = icon_name, active
        self.setToolTip(tip if available else f"{tip} (coming soon)")
        self.setEnabled(available)
        self.setCursor(Qt.CursorShape.PointingHandCursor if available else Qt.CursorShape.ArrowCursor)
        self.setFixedSize(44, 36)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isEnabled() and not self._active and (self.underMouse() or self.isDown()):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(vs.PRESSED if self.isDown() else vs.HOVER)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, 0, -3), 7, 7)
        color = QColor(vs.ICON) if self.isEnabled() else QColor(255, 255, 255, 92)
        side = 20.0
        draw_icon(painter, self._icon, QRectF((self.width() - side) / 2, 6, side, side), color, stroke=1.8)
        if self._active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(QRectF(self.width() / 2 - 11, self.height() - 4, 22, 2), 1, 1)
        painter.end()


class _LoadSignals(QObject):
    ready = Signal(object, object)
    failed = Signal(str)


class _LoadTask(QRunnable):
    def __init__(self, path, signals: _LoadSignals):
        super().__init__()
        self._path, self._signals = path, signals

    def run(self) -> None:
        try:
            try:
                preview, full = ed.load_for_edit(self._path)
            except Exception as error:
                self._signals.failed.emit(str(error))
                return
            self._signals.ready.emit(preview, full)
        except RuntimeError:  # the editor was closed before the photo arrived
            pass


def _button(text: str, name: str = "EdBtn") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


class EditorView(QWidget):
    finished = Signal(object)  # the saved MediaItem, or None when the editor was cancelled

    def __init__(self, item, save: SaveHook, parent: QWidget):
        super().__init__(parent)
        self._item, self._save = item, save
        self._history: list = []
        self._index = -1
        self._ready = False
        self._busy = False
        self._closing = False
        self._mode = "Crop"
        self._preview = None
        self._full: tuple[int, int] = (1, 1)
        self.setObjectName("EditorView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_style())

        self._canvas = EditCanvas()
        self._canvas.changed.connect(self._sync)
        self._canvas.committed.connect(self._commit)
        self._adjust_view = AdjustView()
        self._adjust_panel = AdjustPanel()
        self._adjust_panel.hide()
        self._adjust_panel.changed.connect(self._canvas.set_adjust)  # -> canvas.changed -> _sync
        self._adjust_panel.released.connect(self._commit)
        self._filter_panel = FilterPanel()
        self._filter_panel.hide()
        self._filter_panel.changed.connect(self._canvas.set_filter)
        self._filter_panel.released.connect(self._commit)
        self._bottom = BottomBar()
        self._bottom.setMaximumWidth(940)
        self._bottom.turn.connect(self._canvas.turn)
        self._bottom.flip.connect(self._canvas.flip)
        self._bottom.ratio.connect(self._on_ratio)
        self._bottom.angle_changed.connect(self._canvas.set_angle)
        self._bottom.angle_released.connect(self._commit)

        self._undo = GlyphButton("undo", "Undo (Ctrl+Z)")
        self._redo = GlyphButton("redo", "Redo (Ctrl+Y)")
        self._undo.clicked.connect(self.undo)
        self._redo.clicked.connect(self.redo)
        self._reset = _button("Reset", "EdFlat")
        self._reset.clicked.connect(self.reset)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self._tabs: dict[str, TabButton] = {}
        for name, icon_name in TABS:
            button = TabButton(icon_name, name, active=name == self._mode, available=name in MODES)
            button.clicked.connect(lambda _checked=False, tool=name: self._set_mode(tool))
            self._tabs[name] = button
            tabs.addWidget(button)

        self._save_btn = _button("Save options")
        menu = self._make_menu()
        self._save_btn.setMenu(menu)
        cancel = _button("Cancel")
        cancel.clicked.connect(self.cancel)

        self._header = HeaderBar(item.name)
        self._header.minimize_clicked.connect(lambda: self.window().showMinimized())
        self._header.maximize_clicked.connect(self._toggle_maximized)

        top = QHBoxLayout()
        top.setSpacing(2)
        top.addWidget(self._undo)
        top.addWidget(self._redo)
        top.addWidget(self._reset)
        top.addStretch(1)
        top.addLayout(tabs)
        top.addStretch(1)
        top.addWidget(self._save_btn)
        top.addSpacing(6)
        top.addWidget(cancel)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.addStretch(1)
        middle.addWidget(self._bottom, 100)
        middle.addStretch(1)
        self._bottom_row = QWidget()  # the Crop tool's controls; hidden while another tool is open
        self._bottom_row.setLayout(middle)

        # one picture + one set of controls per tool: Crop = canvas + bottom bar, Adjustment = view + side panel
        self._views = QStackedLayout()
        self._views.addWidget(self._canvas)
        self._views.addWidget(self._adjust_view)
        stage = QHBoxLayout()
        stage.setSpacing(12)
        stage.addLayout(self._views, 1)
        stage.addWidget(self._adjust_panel)
        stage.addWidget(self._filter_panel)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 4, 16, 12)
        root.setSpacing(6)
        root.addWidget(self._header)
        root.addLayout(top)
        root.addLayout(stage, 1)
        root.addWidget(self._bottom_row)

        self._message = QLabel("Loading…", self)
        self._message.setObjectName("EdMessage")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # the close button: its hit area runs on to the window's top-right corner (the viewer asks for it)
        self.corner_close = CornerClose(self, BUTTON)
        self.corner_close.clicked.connect(self.close_window)

        self._set_enabled(False)
        self._signals = _LoadSignals(self)
        self._signals.ready.connect(self._on_ready)
        self._signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(_LoadTask(item.path, self._signals))

    def _make_menu(self) -> QMenu:
        try:
            menu = AnimatedMenu(self)
        except Exception:
            menu = QMenu(self)
        menu.addAction("Save a copy", lambda: self._save_now(True))
        menu.addAction("Save (replace original)", lambda: self._save_now(False))
        return menu

    # // loading

    def _on_ready(self, preview, full) -> None:
        self._message.hide()
        self._preview, self._full = preview, full
        self._canvas.set_source(preview, full)
        self._adjust_view.set_source(preview, full)
        self._ready = True
        self._history = [self._canvas.snapshot()]
        self._index = 0
        self._set_enabled(True)
        self._sync()
        self._canvas.setFocus()

    def _on_failed(self, reason: str) -> None:
        warn(self, "Edit", reason)
        self._close(None)

    def _set_mode(self, name: str) -> None:
        """Switch tool. The edit itself (`EditState`) is shared, so undo / redo / reset cover every tool."""
        if not self._ready or name == self._mode or name not in MODES:
            return
        self._mode = name
        for tool, button in self._tabs.items():
            button.set_active(tool == name)
        showing = name in ("Adjustment", "Filter")
        self._views.setCurrentWidget(self._adjust_view if showing else self._canvas)
        self._adjust_panel.setVisible(name == "Adjustment")
        self._filter_panel.setVisible(name == "Filter")
        self._bottom_row.setVisible(not showing)
        if name == "Filter":
            self._filter_panel.set_source(ed.render_preview(self._preview, self._canvas.state(), self._full, LOOK_SIDE))
        if showing:
            self._adjust_view.show_state(self._canvas.state())
            self.setFocus()
        else:
            self._canvas.setFocus()

    def _set_enabled(self, enabled: bool) -> None:
        self._bottom.setEnabled(enabled)
        self._canvas.setEnabled(enabled)
        self._adjust_panel.setEnabled(enabled)
        self._filter_panel.setEnabled(enabled)
        if not enabled:
            for widget in (self._undo, self._redo, self._reset, self._save_btn):
                widget.setEnabled(False)

    # // history

    def _commit(self) -> None:
        if not self._ready:
            return
        snapshot = self._canvas.snapshot()
        if snapshot == self._history[self._index]:
            return
        del self._history[self._index + 1 :]
        self._history.append(snapshot)
        if len(self._history) > HISTORY_LIMIT:
            del self._history[0]
        self._index = len(self._history) - 1
        self._sync()

    def _go(self, index: int) -> None:
        if self._ready and 0 <= index < len(self._history):
            self._index = index
            self._canvas.restore(self._history[index])  # emits `changed` -> _sync

    def undo(self) -> None:
        self._go(self._index - 1)

    def redo(self) -> None:
        self._go(self._index + 1)

    def reset(self) -> None:
        if not self._ready:
            return
        self._canvas.restore((ed.EditState(), None))
        self._commit()

    def _sync(self) -> None:
        if not self._ready:
            return
        state = self._canvas.state()
        self._bottom.set_angle(state.angle)
        self._adjust_panel.set_values(state.adjust)
        self._filter_panel.set_choice(state.filter)
        if self._mode in ("Adjustment", "Filter"):
            self._adjust_view.show_state(state)
        self._bottom.set_ratio_label(ratio_name(self._canvas.aspect(), self._canvas.original_aspect()))
        changed = not self._canvas.is_unchanged()
        for widget in (self._reset, self._save_btn):
            widget.setEnabled(changed)
        self._undo.setEnabled(self._index > 0)
        self._redo.setEnabled(self._index < len(self._history) - 1)

    def _on_ratio(self, value) -> None:
        if value == "original":
            value = self._canvas.original_aspect()
        self._canvas.set_aspect(value)

    # // finishing

    def _save_now(self, copy: bool) -> None:
        if not self._ready or self._busy or self._canvas.is_unchanged():
            return
        if not copy and not confirm(
            self,
            "Save",
            "This replaces the original photo with the edited one. It can't be undone.",
            ok_text="Save",
            cancel_text="Cancel",
        ):
            return
        self._busy = True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            renewed = self._save(self._item, self._canvas.state(), copy, self)
        finally:
            QApplication.restoreOverrideCursor()
            self._busy = False
        if renewed is not None:  # None: the hook has told the person why it failed; stay open
            self._close(renewed)

    def _confirm_discard(self) -> bool:
        if not self._ready or self._canvas.is_unchanged():
            return True
        return confirm(
            self, "Discard changes?", "The changes to this photo will be lost.", ok_text="Discard", cancel_text="Keep editing"
        )

    def cancel(self) -> None:
        if not self._busy and self._confirm_discard():
            self._close(None)

    def close_window(self) -> None:
        """The title bar's X: leave the editor and close the whole viewer window."""
        if self._busy or not self._confirm_discard():
            return
        window = self.window()
        self._close(None)
        QTimer.singleShot(0, window.close)

    def _toggle_maximized(self) -> None:
        window = self.window()
        if window.isMaximized() or window.isFullScreen():
            window.showNormal()
        else:
            window.showMaximized()

    def _close(self, result) -> None:
        if not self._closing:
            self._closing = True
            self.finished.emit(result)

    # // events

    def _place_corner_close(self) -> None:
        header = self._header.geometry()
        self.corner_close.place(
            header.right() + 1 - BUTTON, header.top() + (header.height() - BUTTON) // 2, self.width()
        )
        self.corner_close.raise_()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_corner_close()
        QTimer.singleShot(0, self._place_corner_close)  # once the layout has settled
        self._canvas.setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._message.setGeometry(self._canvas.geometry())
        self._place_corner_close()
        window = self.window()
        self._header.set_maximized(window.isMaximized() or window.isFullScreen())

    def keyPressEvent(self, event) -> None:
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_Escape:
            self.cancel()
        elif ctrl and key == Qt.Key.Key_Z:
            self.redo() if shift else self.undo()
        elif ctrl and key == Qt.Key.Key_Y:
            self.redo()
        elif ctrl and key == Qt.Key.Key_S:
            QTimer.singleShot(0, lambda: self._save_now(True))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QTimer.singleShot(0, lambda: self._save_now(True))
        event.accept()  # never let a key fall through to the viewer underneath (arrows would change photo)