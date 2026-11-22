"""Small motion helpers: popup menus that fade/slide without DWM ghosting and a red destructive menu row."""

import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QScrollArea, QWidget

from ..theme import theme
from .frost import frosted_backdrop
from .icons import icon

# // Motion tokens
# One place that decides how the app "feels". Durations are in milliseconds; the curves follow
# the Fluent motion model Windows uses: things arriving decelerate (fast start, soft landing),
# things leaving accelerate, and moves between two resting places use the standard curve.
DURATION_FAST = 120
DURATION_NORMAL = 200
DURATION_SLOW = 320
PAGE_FADE_OUT_SHARE = 0.35

# Mouse-wheel scrolling: one notch moves this many pixels and the view glides there. The gallery
# and the Settings page share these so every scroller in the app feels the same.
WHEEL_PIXELS_PER_NOTCH = 130
SCROLL_ANIMATION_MS = 170

# Touchpads (Windows precision touchpads report many small angleDeltas and no pixelDelta) scroll
# the content directly, with no glide, scaled by this factor. Lower = slower. Tune to taste.
TOUCHPAD_SCROLL_SCALE = 0.45
_NOTCH = 120
_PRECISION_STREAM_MS = 200  # events this close after a touchpad event are still the touchpad


class WheelInput:
    """Tells a classic mouse wheel from a touchpad and turns touchpad deltas into pixels/steps.

    A classic wheel always reports whole multiples of 120. Touchpads report fine-grained deltas
    (or pixelDelta on macOS/Linux), so any other value marks the event as precision. Once a
    precision event is seen, the events right after it count as precision too, so a stray
    multiple of 120 in the middle of a swipe or its inertia is not mistaken for a mouse.
    """

    def __init__(self) -> None:
        self._last_precision = 0.0
        self._remainder = 0.0
        self._accum = 0.0
        self._last_step = 0.0

    def is_precision(self, event) -> bool:
        now = time.monotonic()
        delta = event.angleDelta().y() or event.angleDelta().x()
        precision = (
            not event.pixelDelta().isNull()
            or event.phase() != Qt.ScrollPhase.NoScrollPhase
            or (delta != 0 and delta % _NOTCH != 0)
        )
        if precision:
            self._last_precision = now
            return True
        return (now - self._last_precision) * 1000.0 < _PRECISION_STREAM_MS

    def pixels(self, event) -> int:
        """Touchpad delta as whole pixels to scroll (sign: positive = content moves down)."""
        raw = event.pixelDelta().y() if not event.pixelDelta().isNull() else event.angleDelta().y()
        self._remainder += raw * TOUCHPAD_SCROLL_SCALE
        whole = int(self._remainder)  # truncate toward zero, keep the fraction for next event
        self._remainder -= whole
        return whole

    def steps(self, event, threshold: float = 240.0, lockout_ms: int = 300) -> int:
        """Discrete -1/0/+1 steps from a touchpad swipe: one step per gesture, not per event."""
        now = time.monotonic()
        if (now - self._last_step) * 1000.0 < lockout_ms:
            self._accum = 0.0
            return 0
        self._accum += event.angleDelta().y() or event.angleDelta().x()
        if abs(self._accum) < threshold:
            return 0
        step = 1 if self._accum > 0 else -1
        self._accum = 0.0
        self._last_step = now
        return step

    def zoom_units(self, event) -> float:
        """Wheel notches' worth of zoom for this event (touchpad pinch sends small deltas)."""
        return (event.angleDelta().y() or event.angleDelta().x()) / _NOTCH


def _bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


def decelerate() -> QEasingCurve:
    """Fluent "decelerate": elements entering the screen or a new resting place."""
    return _bezier(0.1, 0.9, 0.2, 1.0)


def accelerate() -> QEasingCurve:
    """Fluent "accelerate": elements leaving."""
    return _bezier(0.7, 0.0, 1.0, 0.5)


def standard() -> QEasingCurve:
    """Fluent "standard": moving between two places that are both on screen."""
    return _bezier(0.8, 0.0, 0.2, 1.0)


def make_animation(
    parent: QObject,
    duration: int = DURATION_NORMAL,
    curve: QEasingCurve | None = None,
    start: float = 0.0,
    end: float = 1.0,
) -> "FrameAnimation":
    """A 0→1 (by default) animation with the app's curves; connect valueChanged/finished yourself.

    Ticks once per refresh of the screen it is on (see FrameAnimation), not at Qt's 60 fps cap.
    """
    return FrameAnimation(parent, duration, curve, start, end)


class FrameAnimation(QObject):
    """Time-based 0→1 animation that ticks at the screen's refresh rate (60, 120, 144 Hz...).

    Qt's own animations are capped at ~60 fps. This one asks the timer for one tick per display
    refresh and derives its value from the clock, so a slow frame never slows the motion down.
    valueChanged carries the eased value (start→end, 0→1 by default); finished fires only when
    it reaches the end.
    """

    valueChanged = Signal(float)
    finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        duration: int = DURATION_NORMAL,
        curve: QEasingCurve | None = None,
        start: float = 0.0,
        end: float = 1.0,
    ):
        super().__init__(parent)
        self._duration = max(1, int(duration))
        self._curve = curve if curve is not None else decelerate()
        self._from = float(start)
        self._to = float(end)
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def isRunning(self) -> bool:
        return self._timer.isActive()

    def state(self) -> QAbstractAnimation.State:
        """Same answer QVariantAnimation.state() gave, for callers that ask whether it is running."""
        if self._timer.isActive():
            return QAbstractAnimation.State.Running
        return QAbstractAnimation.State.Stopped

    def setStartValue(self, value: float) -> None:
        self._from = float(value)

    def setEndValue(self, value: float) -> None:
        self._to = float(value)

    def setDuration(self, duration: int) -> None:
        self._duration = max(1, int(duration))

    def setEasingCurve(self, curve: QEasingCurve) -> None:
        self._curve = curve

    def _interval(self) -> int:
        screen = None
        getter = getattr(self.parent(), "screen", None)
        if callable(getter):
            screen = getter()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        rate = float(screen.refreshRate()) if screen is not None else 60.0
        if rate < 30.0:
            rate = 60.0
        return max(4, int(round(1000.0 / min(rate, 240.0))))

    def start(self) -> None:
        self._started = time.perf_counter()
        self._timer.start(self._interval())

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        progress = (time.perf_counter() - self._started) * 1000.0 / self._duration
        if progress >= 1.0:
            self._timer.stop()
            self.valueChanged.emit(self._to)
            self.finished.emit()
            return
        eased = float(self._curve.valueForProgress(progress))
        self.valueChanged.emit(self._from + (self._to - self._from) * eased)


# Every dropdown in Settings (theme, mouse wheel, zoom preference) and the About expander
# share this duration, which matches the Info card of the photo viewer. Change this one
# constant to retime them all.
DROPDOWN_DURATION = DURATION_NORMAL + 40
DROPDOWN_SLIDE = 12

# Menus use the same timing as the Info card so everything in the app moves at one pace.
MENU_DURATION = DURATION_NORMAL
PHOTO_MENU_DURATION = DURATION_NORMAL
MENU_SLIDE = 8
PHOTO_MENU_SLIDE = 12
MENU_EDGE = 8
_HOVER_CANCEL_PX = 4
_MODIFIER_KEYS = (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr)


def _top_level_window(widget: QWidget | None) -> QWidget | None:
    current = widget
    while current is not None:
        if current.isWindow() and not (current.windowFlags() & Qt.WindowType.Popup):
            return current
        current = current.parentWidget()
    return None


def opens_upward(top: int, height: int, pointer_y: int) -> bool:
    """True when a menu at [top, top+height) was opened from a point below its middle.

    A menu opened from a click or a button *above* it unfolds downward (the pointer sits at or
    above its top edge). A menu that did not fit below the pointer is placed above it, so the
    pointer sits at its bottom edge and it grows upward instead. The small tolerance below the
    menu covers a pointer that ended up just under the bottom edge.
    """
    return top + height / 2 < pointer_y <= top + height + 40


MENU_RADIUS = 8  # keep in step with QMenu border-radius in theme.py


class AnimatedMenu(QMenu):
    """Popup menu that unfolds from the edge it was opened from, without moving its native HWND.

    Opened downward (toolbar buttons, right-click with room below) it drops down from its top
    edge. Opened upward (right-click near the bottom) it rises from its bottom edge, so the
    motion always starts at the pointer.
    """

    def __init__(self, parent=None, duration: int = MENU_DURATION, slide: int = MENU_SLIDE):
        super().__init__(parent)
        self._duration = max(0, int(duration))
        self._slide = max(0, int(slide))
        # Frameless + translucent so the stylesheet's rounded corners are real.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._animation: FrameAnimation | None = None
        self._animating = False
        self._upward = False
        self._progress = 1.0
        self._snapshot: QPixmap | None = None
        self._hidden_children: list[QWidget] = []
        self._open_cursor_pos: QPoint | None = None
        self._centre_widget: QWidget | None = None
        self._backdrop: QPixmap | None = None  # blurred window content behind the menu (acrylic look)

    def centre_on(self, widget: QWidget | None) -> None:
        """Open this menu horizontally centred under ``widget`` (a toolbar button) instead of
        left-aligned with it. It is still kept inside the window and the screen."""
        self._centre_widget = widget

    @property
    def animation_duration(self) -> int:
        return self._duration

    @property
    def slide_distance(self) -> int:
        return self._slide

    @property
    def is_animating(self) -> bool:
        return self._animating

    @property
    def opens_upward(self) -> bool:
        """Direction chosen for the current (or last) popup: True = grows from the bottom edge."""
        return self._upward

    def setVisible(self, visible: bool) -> None:
        if not visible:
            self._stop_animation()
            super().setVisible(False)
            return

        self._stop_animation()
        self.ensurePolished()
        self.adjustSize()
        end, self._upward = self._resolve_placement(self.pos(), self.size(), QCursor.pos())
        self.move(end)
        self._backdrop = self._frosted_backdrop(end)

        if self._duration <= 0:
            super().setVisible(True)
            return

        # Render the polished menu into an offscreen buffer, then reveal the stationary
        # native window at opacity 0.0 internally so DWM never sees HWND jumps or alpha lag.
        self._snapshot = self.grab()
        self._hidden_children = [
            child
            for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
            if not child.isHidden()
        ]
        for child in self._hidden_children:
            child.hide()

        self._progress = 0.0
        self._animating = True
        self._open_cursor_pos = QCursor.pos()
        super().setVisible(True)

        animation = FrameAnimation(self, self._duration, decelerate())
        animation.valueChanged.connect(self._on_step)
        animation.finished.connect(self._finish_animation)
        self._animation = animation
        animation.start()

    def _follows_float_opacity(self) -> bool:
        """Same test as the stylesheet's `QMainWindow QMenu, QMenu[floatMenu="true"]` rule."""
        if self.property("floatMenu"):
            return True
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QMainWindow):
                return True
            widget = widget.parentWidget()
        return False

    def _frosted_backdrop(self, top_left: QPoint) -> QPixmap | None:
        """Blurred picture of what is under the menu; only needed once the menu is see-through."""
        if theme.float_opacity() >= 1.0 or not self._follows_float_opacity():
            return None
        anchor = self.parentWidget()
        while isinstance(anchor, QMenu):  # a submenu is blurred over the window, like its parent menu
            anchor = anchor.parentWidget()
        window = anchor.window() if anchor is not None else None
        try:
            return frosted_backdrop(window, top_left, self.size())
        except Exception:
            return None

    def _paint_backdrop(self) -> None:
        if self._backdrop is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), MENU_RADIUS - 1, MENU_RADIUS - 1)
        painter.setClipPath(shape)
        painter.drawPixmap(0, 0, self._backdrop)

    def _on_step(self, value) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    def _finish_animation(self) -> None:
        if not self._animating and self._animation is None:
            return
        self._stop_animation()
        self.update()

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self._animating = False
        self._progress = 1.0
        self._snapshot = None
        for child in self._hidden_children:
            child.show()
        self._hidden_children.clear()

    def paintEvent(self, event) -> None:
        if self._animating and self._snapshot is not None:
            painter = QPainter(self)
            # Clear the translucent backing store completely so prior frames never leave ghost trails.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            # The window itself never changes size; a growing clip rect unfolds the snapshot from
            # the edge the menu was opened from, and the content eases a little toward its place.
            height, width = self.height(), self.width()
            visible = max(1, round(height * self._progress))
            drift = round(min(height * 0.3, self._slide * 4) * (1.0 - self._progress))
            if self._upward:
                painter.setClipRect(0, height - visible, width, visible)
                painter.drawPixmap(0, drift, self._snapshot)
            else:
                painter.setClipRect(0, 0, width, visible)
                painter.drawPixmap(0, -drift, self._snapshot)
            return
        self._paint_backdrop()  # under the menu's own (translucent) background
        super().paintEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._animating:
            if (
                self._open_cursor_pos is not None
                and (event.globalPosition().toPoint() - self._open_cursor_pos).manhattanLength() >= _HOVER_CANCEL_PX
            ):
                self._finish_animation()
            else:
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._animating:
            global_pos = event.globalPosition().toPoint()
            self._finish_animation()
            local_pos = self.mapFromGlobal(global_pos)
            target = self.childAt(local_pos)
            if target is not None and target is not self:
                forwarded = QMouseEvent(
                    event.type(),
                    QPointF(target.mapFromGlobal(global_pos)),
                    event.globalPosition(),
                    event.button(),
                    event.buttons(),
                    event.modifiers(),
                )
                QApplication.sendEvent(target, forwarded)
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._animating:
            self._finish_animation()
        if self._trigger_shortcut(event):
            return
        super().keyPressEvent(event)

    def _trigger_shortcut(self, event) -> bool:
        """Run the item whose shortcut was pressed while this popup is open.

        A popup does not become the active window, so the window-level shortcuts are suspended
        while it is shown and Qt will not fire the shortcuts printed in the rows. The menu
        therefore handles them itself, on the items it was built for.
        """
        key = event.key()
        if key in _MODIFIER_KEYS:
            return False
        # Enter on a row the user already highlighted (arrows / hover) runs that row, not Open.
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.activeAction() is not None:
            return False
        pressed = QKeySequence(event.keyCombination())
        for action in self.actions():
            if not action.isEnabled() or action.isSeparator():
                continue
            for shortcut in action.shortcuts():
                if not shortcut.isEmpty() and shortcut.matches(pressed) == QKeySequence.SequenceMatch.ExactMatch:
                    event.accept()
                    self.close()
                    action.trigger()
                    return True
        return False

    def _resolve_placement(self, natural: QPoint, size: QSize, pointer: QPoint) -> tuple[QPoint, bool]:
        """Final top-left of the menu and whether it grows upward.

        Keeps the menu inside the app window and the screen. A menu whose top edge sits at the
        pointer (right-click) but does not fit below it is flipped to sit above the pointer, so it
        rises from the click instead of being shoved up over it.
        """
        menu_size = size if size is not None and size.isValid() else self.size()
        width, height = menu_size.width(), menu_size.height()
        x, y = natural.x(), natural.y()
        upward = False
        centre = self._centre_widget
        if centre is not None and centre.isVisible():
            middle = centre.mapToGlobal(QPoint(centre.width() // 2, 0)).x()
            x = middle - width // 2
        anchor = self.parentWidget()
        is_submenu = isinstance(anchor, QMenu)
        window = _top_level_window(anchor)
        if not is_submenu and window is not None and window.isVisible():
            bounds: QRect = window.frameGeometry().adjusted(MENU_EDGE, MENU_EDGE, -MENU_EDGE, -MENU_EDGE)
            at_pointer = abs(y - pointer.y()) <= 4
            if at_pointer and y + height - 1 > bounds.bottom() and pointer.y() - height + 1 >= bounds.top():
                y = pointer.y() - height
                upward = True
            x = max(bounds.left(), min(x, bounds.right() - width + 1))
            y = max(bounds.top(), min(y, bounds.bottom() - height + 1))

        screen = QGuiApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry().adjusted(4, 4, -4, -4)
            x = max(avail.left(), min(x, avail.right() - width + 1))
            y = max(avail.top(), min(y, avail.bottom() - height + 1))

        # Qt (or the clamps above) may already have moved the menu up, away from where it was asked.
        if y < natural.y() or opens_upward(y, height, pointer.y()):
            upward = True
        return QPoint(x, y), upward

    def hideEvent(self, event) -> None:
        self._stop_animation()
        self._backdrop = None
        super().hideEvent(event)


def add_danger_action(
    menu: QMenu,
    text: str,
    icon_name: str = "trash",
    shortcut_text: str = "",
) -> QAction:
    """Add a red destructive row that is a plain QMenu item, so it shares the exact grid
    (icon / label / shortcut columns, paddings, hover pill) of every other row.

    QSS cannot colour a single item directly, so the row is marked as the menu's default action
    and the stylesheet paints `QMenu::item:default` in the danger colour (theme.py).
    """
    from ..theme import theme

    action = QAction(icon(icon_name, theme.palette().danger), text, menu)
    if shortcut_text:
        action.setShortcut(QKeySequence(shortcut_text))
    menu.addAction(action)
    menu.setDefaultAction(action)
    return action


class PageTransition(QObject):
    """Cross-fade + gentle slide between two pages of a QStackedWidget.

    Both pages are rendered to pixmaps and painted by an overlay, so the real widgets never
    resize or ghost while the animation runs; at the end the overlay disappears and the real
    page is already showing.
    """

    def __init__(self, stack, duration: int = DURATION_SLOW, slide: int = 24, exit_slide: int = 0, background=None):
        super().__init__(stack)
        self._stack = stack
        # background(painter, widget, rect) -> bool paints what lies behind the pages (a picture, say) and
        # returns False when it has nothing to show, in which case the overlay uses the plain content color
        self.background = background
        self._duration = duration
        self._slide = slide
        self._exit_slide = exit_slide  # how far the old page drifts the other way while it fades out
        self._direction = 1
        self._overlay: QWidget | None = None
        self._animation: FrameAnimation | None = None
        self._old = QPixmap()
        self._new = QPixmap()
        self._progress = 1.0
        self._target: int | None = None

    @property
    def running(self) -> bool:
        return self._animation is not None

    def switch_to(self, index: int, direction: int = 1) -> None:
        """Show page `index`. direction > 0 brings it in from below, < 0 from above."""
        stack = self._stack
        if self._animation is not None:
            self._finish()
        self._direction = 1 if direction >= 0 else -1
        if index == stack.currentIndex() or not stack.isVisible() or self._duration <= 0:
            stack.setCurrentIndex(index)
            return
        target = stack.widget(index)
        current = stack.currentWidget()
        size = stack.size()
        target.resize(size)
        layout = target.layout()
        if layout is not None:
            layout.activate()
        prepare = getattr(target, "prepare_for_display", None)
        if callable(prepare):
            prepare()  # a page that was never shown must be at its final layout before it is grabbed
        self._old = current.grab()
        self._new = target.grab()
        self._target = index

        overlay = _TransitionOverlay(self, stack)
        overlay.setGeometry(stack.rect())
        overlay.show()
        overlay.raise_()
        self._overlay = overlay
        stack.setCurrentIndex(index)
        overlay.raise_()
        self._progress = 0.0
        animation = FrameAnimation(self, self._duration, decelerate())
        animation.valueChanged.connect(self._on_step)
        animation.finished.connect(self._finish)
        self._animation = animation
        animation.start()

    def _on_step(self, value: float) -> None:
        self._progress = value
        if self._overlay is not None:
            self._overlay.update()

    def _finish(self) -> None:
        animation, self._animation = self._animation, None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        overlay, self._overlay = self._overlay, None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
        self._old = QPixmap()
        self._new = QPixmap()
        self._progress = 1.0

    def paint(self, painter: QPainter, rect: QRect) -> None:
        t = max(0.0, min(1.0, self._progress))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if t < PAGE_FADE_OUT_SHARE:
            if not self._old.isNull():
                painter.setOpacity(1.0 - t / PAGE_FADE_OUT_SHARE)
                painter.drawPixmap(0, round(-self._exit_slide * self._direction * t), self._old)
            return
        u = (t - PAGE_FADE_OUT_SHARE) / (1.0 - PAGE_FADE_OUT_SHARE)
        if not self._new.isNull():
            painter.setOpacity(u)
            painter.drawPixmap(0, round(self._slide * self._direction * (1.0 - u)), self._new)


class _TransitionOverlay(QWidget):
    def __init__(self, owner: PageTransition, parent: QWidget):
        super().__init__(parent)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def paintEvent(self, event) -> None:
        from ..theme import theme

        painter = QPainter(self)
        background = self._owner.background
        if background is None or not background(painter, self, self.rect()):
            painter.fillRect(self.rect(), QColor(theme.palette().content))
        self._owner.paint(painter, self.rect())


class SmoothWheelMixin:
    """Mouse-wheel glide for any scroll area (QScrollArea, QTreeView, ...).

    Same feel as the gallery: each notch moves a fixed distance, quick notches stack onto the
    running glide, and touchpads (which already send smooth pixel deltas) are left alone.
    Dragging the scroll bar or using the keyboard stops the glide so they never fight it.
    Call `_init_glide()` once from __init__, after the base class is constructed. Views that
    scroll per item (QTreeView) must also be switched to ScrollPerPixel by the caller.
    """

    def _init_glide(self) -> None:
        self._glide_from = 0
        self._glide_target = 0
        self._glide = make_animation(self, SCROLL_ANIMATION_MS, QEasingCurve(QEasingCurve.Type.OutCubic), 0.0, 1.0)
        self._glide.valueChanged.connect(self._on_glide)
        self.verticalScrollBar().actionTriggered.connect(lambda *_: self._glide.stop())
        self._wheel_input = WheelInput()

    def _on_glide(self, progress: float) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(round(self._glide_from + (self._glide_target - self._glide_from) * progress))

    def scroll_to(self, value: int) -> None:
        """Glide to `value` (clamped to the scroll range)."""
        bar = self.verticalScrollBar()
        self._glide_target = max(bar.minimum(), min(bar.maximum(), int(value)))
        self._glide_from = bar.value()
        if self._glide_target == self._glide_from:
            self._glide.stop()
            return
        self._glide.stop()
        self._glide.start()

    def wheelEvent(self, event) -> None:
        if event.modifiers() or event.angleDelta().y() == 0:
            super().wheelEvent(event)
            return
        bar = self.verticalScrollBar()
        if bar.maximum() <= bar.minimum():
            event.ignore()  # nothing to scroll
            return
        if self._wheel_input.is_precision(event):
            self._glide.stop()
            bar.setValue(bar.value() - self._wheel_input.pixels(event))
            event.accept()
            return
        base = self._glide_target if self._glide.isRunning() else bar.value()
        self.scroll_to(base - round(event.angleDelta().y() * WHEEL_PIXELS_PER_NOTCH / 120))
        event.accept()


class SmoothScrollArea(SmoothWheelMixin, QScrollArea):
    """QScrollArea whose mouse wheel glides instead of jumping a few lines at a time."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._init_glide()