"""The immersive photo viewer: a standalone window with floating, auto-hiding controls."""

import os
import random
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsOpacityEffect, QLabel, QMenu, QWidget

from ...services.scanner import MediaItem
from ..common.branding import style_title_bar
from ..common.corner_close import CornerClose
from ..common.dialogs import warn
from ..common.motion import DURATION_NORMAL, AnimatedMenu, decelerate, make_animation
from ..theme.preferences import prefs
from . import frame
from . import geometry as geo
from .canvas import EDGE_ZONE, ZOOM_FACTOR, ImageCanvas
from .filmstrip import Filmstrip
from .info_card import InfoCard
from .loader import ViewData, ViewerLoader
from .overlays import ActionsPill, ChromeFader, CounterPill, NavButton, TitlePill, ToolsPill, WindowControls, ZoomPill
from .video import BAR_HEIGHT as VIDEO_BAR_HEIGHT
from .video import BAR_MAX_WIDTH as VIDEO_BAR_MAX_WIDTH
from .video import VideoBar, VideoSurface
from .style import (
    BOTTOM_HEIGHT,
    CAPTION,
    GAP,
    IDLE_MS,
    MARGIN,
    MIN_WINDOW,
    STRIP_HEIGHT,
    TOP_HEIGHT,
)

ZOOM_EDGE_PX = 80  # while zoomed with the controls hidden, the mouse this close to the top / bottom edge shows them

NAV_POLL_MS = 33
IMMERSIVE_BAR_MAX_WIDTH = 880
IMMERSIVE_IDLE_MS = 2000
SLIDESHOW_INTERVAL_MS = 4000
ROTATE_SETTLE_MS = 600  # clicks on Rotate are gathered into one rewrite of the file
STRIP_MAX_WIDTH = 780
STRIP_MIN_WIDTH = 220


@dataclass
class ViewerHooks:
    """What the viewer needs from the gallery. Every hook is optional; the matching button
    simply disappears when its hook is missing."""

    is_favorite: Callable[[MediaItem], bool] | None = None
    toggle_favorite: Callable[[MediaItem], None] | None = None
    delete: Callable[[MediaItem, QWidget], bool] | None = None  # True when the file was deleted
    edit: Callable[[MediaItem], None] | None = None
    # rename(item, new name without extension, parent window) -> the renamed item, or None when
    # the rename was refused (the hook shows its own message).
    rename: Callable[[MediaItem, str, QWidget], MediaItem | None] | None = None
    # rotate(item, clockwise degrees, parent window) -> the item as it is after the file itself
    # was rotated, or None when that failed (the hook shows its own message). Without the hook
    # Rotate only turns the view.
    rotate: Callable[[MediaItem, int, QWidget], MediaItem | None] | None = None
    # save_edit(item, edit state, as a copy?, parent window) -> the item as it is after the edit was
    # written (a new item for a copy), or None when saving failed (the hook shows its own message).
    # Without the hook Edit falls back to `edit` (an external program).
    save_edit: Callable[..., MediaItem | None] | None = None
    set_sidebar_background: Callable[[MediaItem], None] | None = None
    # set_wallpaper(item, parent window): sets the desktop wallpaper (the hook shows its own error).
    set_wallpaper: Callable[[MediaItem, QWidget], None] | None = None
    thumbnail_for: Callable[[MediaItem], QPixmap | None] | None = None
    thumbnails_changed: object | None = None  # a Qt signal, fired when thumbnails arrive


PLAY_MODES = ("once", "repeat", "order", "shuffle")


class ViewerWindow(QWidget):
    closed = Signal()

    def __init__(
        self,
        items: list[MediaItem],
        row: int,
        parent: QWidget | None = None,
        slideshow: bool = False,
        hooks: ViewerHooks | None = None,
    ):
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        if not frame.IS_WINDOWS:
            flags |= Qt.WindowType.FramelessWindowHint  # Windows keeps its native frame; see frame.py
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(*MIN_WINDOW)
        self.resize(1200, 800)
        self._hooks = hooks or ViewerHooks()
        self._items: list[MediaItem] = list(items)
        self._row = max(0, min(row, len(self._items) - 1))
        self._current: ViewData | None = None
        self._menu_open = False
        self._zoom_hidden = False  # controls stay hidden while the photo is zoomed (optional setting)
        self._was_maximized = False
        self._title_bar_styled = False
        self._last_cursor = QCursor.pos()
        self._info_open = False
        self._hidden_keys: set[str] = set()
        self._rot_item: MediaItem | None = None  # photo whose file still has to be rotated
        self._rot_degrees = 0
        self._editor = None  # the in-app editor while it is open (covers the whole viewer)

        self._video: VideoSurface | None = None  # created when the first video is shown
        self._video_mode = False
        mode = str(prefs().get("video_play_mode"))  # remembered between sessions
        if mode not in PLAY_MODES:
            mode = "repeat" if prefs().get("video_loop") else "once"  # the old loop toggle
        self._play_mode = mode
        self._shuffle_trail: list[str] = []  # the videos shuffle has played, the current one last
        self._shuffle_pos = -1
        self._shuffle_seen: set[str] = set()  # played in this round: a round plays every video once
        self._auto_nav = False
        self._volume = min(100, max(0, int(prefs().get("video_volume"))))  # remembered between sessions
        self._muted = bool(prefs().get("video_muted"))
        self._immersive_state = False
        self._cursor_hidden = False
        self._canvas = ImageCanvas(self)
        self._canvas.step_requested.connect(self._step)
        self._canvas.gesture_zoomed.connect(self._on_gesture_zoomed)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.pressed.connect(self._reclaim_focus)
        self._canvas.caption_hit = self._is_caption_at
        self._canvas.caption_pressed.connect(self._start_move)
        self._canvas.caption_double_clicked.connect(self._toggle_maximized)
        self._message = QLabel(self)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setStyleSheet("color: rgba(255,255,255,170); font-size: 15px; background: transparent;")
        self._message.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._message.hide()

        self._title = TitlePill(self, TOP_HEIGHT)
        self._tools = ToolsPill(self, TOP_HEIGHT)
        self._tools.set_available("delete", self._hooks.delete is not None)
        self._tools.set_available("wallpaper", self._hooks.set_wallpaper is not None)
        self._tools.triggered.connect(self._on_toolbar)
        self._actions = ActionsPill(self, TOP_HEIGHT)
        self._actions.set_available("favorite", self._hooks.toggle_favorite is not None)
        self._actions.set_available("edit", self._hooks.edit is not None)
        self._actions.triggered.connect(self._on_toolbar)
        self._controls = WindowControls(self, TOP_HEIGHT)
        self._controls.minimize_clicked.connect(self.showMinimized)
        self._controls.maximize_clicked.connect(self._toggle_maximized)
        self._close_zone = CornerClose(self)  # the close button; its hit area runs on to the window's corner
        self._close_zone.clicked.connect(self.close)
        self._prev = NavButton("chevron-left", self)
        self._prev.clicked.connect(lambda: self._step(-1))
        self._next = NavButton("chevron-right", self)
        self._next.clicked.connect(lambda: self._step(1))
        self._counter = CounterPill(self, BOTTOM_HEIGHT)
        self._zoom = ZoomPill(self, BOTTOM_HEIGHT)
        self._zoom.zoom_in.connect(lambda: self._canvas.zoom_smooth(ZOOM_FACTOR))
        self._zoom.zoom_out.connect(lambda: self._canvas.zoom_smooth(1 / ZOOM_FACTOR))
        self._zoom.toggle_zoom.connect(self._toggle_zoom)
        self._zoom.fit.connect(lambda: self._canvas.fit(upscale=True))
        self._zoom.fullscreen.connect(self._toggle_fullscreen)
        self._canvas.zoom_changed.connect(self._zoom.set_percent)
        self._strip = Filmstrip(self)
        self._strip.set_thumbnail_source(self._hooks.thumbnail_for)
        self._strip.row_requested.connect(self._show_row)
        self._strip.set_items(self._items, self._row)
        if self._hooks.thumbnails_changed is not None:
            self._hooks.thumbnails_changed.connect(self._on_thumbnails_changed)

        self._video_bar = VideoBar(self, VIDEO_BAR_HEIGHT)
        self._video_bar.play_clicked.connect(self._toggle_video)
        self._video_bar.seek_requested.connect(self._seek_video)
        self._video_bar.mute_clicked.connect(self._toggle_mute)
        self._video_bar.volume_changed.connect(self._on_volume)
        self._video_bar.play_mode_clicked.connect(self._cycle_play_mode)
        self._video_bar.previous_clicked.connect(lambda: self._bar_step(-1))
        self._video_bar.next_clicked.connect(lambda: self._bar_step(1))

        self._info = InfoCard(self)
        self._info.close_requested.connect(lambda: self._set_info(False))
        self._info.set_renamable(self._hooks.rename is not None)
        self._info.rename_requested.connect(self._on_rename)
        self._info.edit_finished.connect(self._reclaim_focus)
        self._info.hide()
        self._info_height = 300
        self._info_progress = 0.0
        self._info_effect = QGraphicsOpacityEffect(self._info)
        self._info_effect.setOpacity(0.0)
        self._info.setGraphicsEffect(self._info_effect)
        self._info_animation = make_animation(self, DURATION_NORMAL, decelerate(), 0.0, 1.0)
        self._info_animation.valueChanged.connect(self._on_info_progress)
        self._info_animation.finished.connect(self._on_info_finished)

        self._chrome = [
            self._title, self._tools, self._actions, self._controls, self._close_zone, self._counter, self._zoom,
            self._strip, self._video_bar,
        ]
        self._fader = ChromeFader(self._chrome, self)
        self._prev_fader = ChromeFader([self._prev], self)
        self._next_fader = ChromeFader([self._next], self)
        self._prev_fader.snap(0.0)
        self._next_fader.snap(0.0)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._on_idle)
        # Cheap mouse-activity detection: poll the cursor a few times a second instead of
        # filtering every event of the application through Python.
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(120)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._nav_state = 0
        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(NAV_POLL_MS)
        self._nav_timer.timeout.connect(self._update_nav_zone)

        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)
        self.setMouseTracking(True)
        self._canvas.viewport().setMouseTracking(True)
        for widget in (self._message, self._strip, self._info):
            widget.raise_()
        for widget in (*self._chrome, self._prev, self._next):
            widget.raise_()
        self._info.raise_()

        self._loader = ViewerLoader(self, with_location=bool(prefs().get("location_features")))
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)

        self._rot_timer = QTimer(self)
        self._rot_timer.setSingleShot(True)
        self._rot_timer.setInterval(ROTATE_SETTLE_MS)
        self._rot_timer.timeout.connect(self._flush_rotation)

        self._slideshow = QTimer(self)
        self._slideshow.setInterval(SLIDESHOW_INTERVAL_MS)
        self._slideshow.timeout.connect(lambda: self._advance(from_timer=True))

        self._relayout()
        self._show_row(self._row)
        if slideshow:
            self._slideshow.start()
            self._tools.set_checked("slideshow", True)
            self.showFullScreen()
        self._grips = frame.build_grips(self)
        prefs().changed.connect(self._on_pref_changed)
        self._start_idle()

    # // window plumbing

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._title_bar_styled:
            self._title_bar_styled = True
            style_title_bar(self, caption=CAPTION)
            frame.apply_frame(self)
            frame.round_corners(self)
        self._last_cursor = QCursor.pos()
        self._cursor_timer.start()
        self._nav_timer.start()
        self.setFocus()

    def closeEvent(self, event) -> None:
        self._flush_rotation()
        try:
            prefs().changed.disconnect(self._on_pref_changed)
        except (RuntimeError, TypeError):
            pass
        self._idle.stop()
        self._set_cursor_hidden(False)
        self._slideshow.stop()
        self._info_animation.stop()
        self._cursor_timer.stop()
        self._nav_timer.stop()
        self._loader.shutdown()
        # Release the pixels now. The window object is deleted later, and its Python wrapper can
        # outlive that, so nothing big may stay reachable from it.
        self._current = None
        self._canvas.clear_image()
        if self._video is not None:
            self._video.release()
        self._strip.set_thumbnail_source(None)
        self.closed.emit()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._canvas.setGeometry(self.rect())
        if self._video is not None:
            self._video.setGeometry(self.rect())
        if self._editor is not None:
            self._editor.setGeometry(self.rect())
        self._relayout()
        if hasattr(self, "_grips"):
            frame.place_grips(self, self._grips)
        frame.refresh_corner_region(self)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._zoom.set_fullscreen(self.isFullScreen())
            self._controls.set_maximized(self.isMaximized() or self.isFullScreen())
            frame.refresh_corner_region(self)
            self._relayout()
            if hasattr(self, "_grips"):
                frame.place_grips(self, self._grips)

    def nativeEvent(self, event_type, message):
        result = frame.handle_native_event(self, event_type, message, self._is_caption_at, self._over_close)
        if result is not None:
            return result
        return super().nativeEvent(event_type, message)

    def _over_close(self, point: QPoint) -> bool:
        """True over the close button, its stretch to the corner included: the edge grips must not take those pixels."""
        zone = self._editor.corner_close if self._editor is not None else self._close_zone
        if not zone.isVisible() or zone.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return False
        return QRect(zone.mapTo(self, QPoint(0, 0)), zone.size()).contains(point)

    # The top row doubles as the title bar: an empty part of it drags the window and a double-click
    # maximizes it (Windows answers this itself through WM_NCHITTEST, see frame.py).
    def _is_caption_at(self, point: QPoint) -> bool:
        if self.isFullScreen() or not self._fader.is_shown():
            return False
        if point.y() < 0 or point.y() >= MARGIN + TOP_HEIGHT + GAP:
            return False
        child = self.childAt(point)
        if child is None or child in (self._canvas, self._canvas.viewport(), self._message, self._video):
            return True
        return bool(child.property("dragRegion"))

    def _start_move(self) -> None:
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_caption_at(event.position().toPoint()):
            self._start_move()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_caption_at(event.position().toPoint()):
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _toggle_maximized(self) -> None:
        if self.isFullScreen():
            self._toggle_fullscreen()  # back to the window it came from
        elif self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._cursor_timer.stop()
        self._nav_timer.stop()
        self._set_cursor_hidden(False)
        self._nav_state = 0
        self._prev_fader.snap(0.0)
        self._next_fader.snap(0.0)

    def _poll_cursor(self) -> None:
        position = QCursor.pos()
        if position == self._last_cursor:
            return
        self._last_cursor = position
        if self._zoom_hidden:
            self._follow_zoom_edges(position)
            return
        if not self.isMinimized() and self.frameGeometry().contains(position):
            self._poke()

    def _nav_zone(self) -> int:
        position = QCursor.pos()
        widget = QApplication.widgetAt(position)
        width = self.width()
        if widget is None or widget.window() is not self or width <= 0:
            return 0
        x = self.mapFromGlobal(position).x()
        left_limit = max(width * EDGE_ZONE, MARGIN + self._prev.width())
        right_limit = min(width * (1.0 - EDGE_ZONE), width - MARGIN - self._next.width())
        if self._prev_fader.is_shown():
            left_limit = max(left_limit, self._prev.x() + self._prev.width() + GAP)
        if self._next_fader.is_shown():
            right_limit = min(right_limit, self._next.x() - GAP)
        if x <= left_limit:
            return -1
        if x >= right_limit:
            return 1
        return 0

    def _update_nav_zone(self) -> None:
        zone = 0 if self._zoom_hidden else self._nav_zone()
        if zone == self._nav_state:
            return
        self._nav_state = zone
        self._prev_fader.fade_to(1.0 if zone < 0 else 0.0)
        self._next_fader.fade_to(1.0 if zone > 0 else 0.0)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    # // layout

    def _relayout(self) -> None:
        width, height = self.width(), self.height()
        margin = MARGIN
        self._canvas.setGeometry(0, 0, width, height)
        if self._video is not None:
            self._video.setGeometry(0, 0, width, height)
        self._message.setGeometry(width // 6, height // 2 - 60, width * 2 // 3, 120)
        self._arrange_top()

        bottom = height - margin
        self._counter.move(margin, bottom - self._counter.height())
        self._zoom.move(width - margin - self._zoom.width(), bottom - self._zoom.height())
        side = max(self._counter.width(), self._zoom.width()) + margin + GAP * 2
        room = width - 2 * side
        if room >= STRIP_MIN_WIDTH and self._items:
            strip_width = self._strip.preferred_width(min(room, STRIP_MAX_WIDTH))
            self._strip.setFixedWidth(max(strip_width, 120))
            self._strip.move((width - self._strip.width()) // 2, bottom - STRIP_HEIGHT)
            self._strip.show()
        else:
            self._strip.hide()

        self._next.move(width - margin - self._next.width(), (height - self._next.height()) // 2)
        bar_width = max(160, min(VIDEO_BAR_MAX_WIDTH, width - 2 * margin))
        if self._immersive():
            bar_width = max(160, min(IMMERSIVE_BAR_MAX_WIDTH, width - 2 * margin))
        self._video_bar.setFixedWidth(bar_width)
        row_height = STRIP_HEIGHT if not self._strip.isHidden() else BOTTOM_HEIGHT
        self._video_bar.move((width - bar_width) // 2, height - margin - row_height - GAP - VIDEO_BAR_HEIGHT)
        self._place_info()
        self._apply_insets()
        self._enforce_immersive()

    def _immersive(self) -> bool:
        return self.isFullScreen() and self._video_mode

    def _enforce_immersive(self) -> None:
        immersive = self._immersive()
        self._video_bar.set_skip_visible(immersive)
        for widget in (self._tools, self._controls, self._close_zone):
            widget.setVisible(not immersive)
        if immersive:
            for widget in (self._title, self._actions):
                widget.hide()
        if immersive != self._immersive_state:
            self._immersive_state = immersive
            self._set_cursor_hidden(False)
            self._fader.fade_to(1.0)
            self._start_idle()

    def _set_cursor_hidden(self, hidden: bool) -> None:
        if hidden == self._cursor_hidden:
            return
        self._cursor_hidden = hidden
        if hidden:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
        else:
            QApplication.restoreOverrideCursor()

    def _arrange_top(self) -> None:
        """Top row: tools on the left, the title centred on the window, actions and the window
        buttons on the right."""
        width, margin = self.width(), MARGIN
        has_edit = self._actions.has_edit
        self._hidden_keys = geo.plan_top(
            width, self._tools.keys(), self._actions.keys(), geo.COLLAPSE_ORDER, has_edit, margin, GAP
        )
        self._tools.arrange(self._hidden_keys)
        self._actions.arrange(self._hidden_keys)
        self._tools.move(margin, margin)
        self._controls.move(width - margin - self._controls.width(), margin)
        right_start = self._controls.x()
        if self._actions.has_content():
            self._actions.move(right_start - GAP - self._actions.width(), margin)
            right_start = self._actions.x()
        left_end = self._tools.x() + self._tools.width()
        x, span = geo.title_span(width, left_end, right_start, self._title.natural_width(), GAP)
        self._title.setVisible(span >= geo.TITLE_FLOOR)
        if span >= geo.TITLE_FLOOR:
            self._title.fit(span)
            self._title.move(x, margin)
        spot = self._controls.pos() + self._controls.close_rect().topLeft()
        self._close_zone.place(spot.x(), spot.y(), width)
        self._enforce_immersive()

    def _apply_insets(self) -> None:
        """Keep the resting ("fit") photo clear of the floating rows and the Info card. Zoomed
        photos still use the whole window, and the controls fading away never re-fits."""
        bottom_height = STRIP_HEIGHT if not self._strip.isHidden() else BOTTOM_HEIGHT
        progress = self._info_progress
        if self._video_mode:
            progress = 1.0 if self._info_open or self._info_animation.isRunning() else 0.0
        left, top, right, bottom = geo.safe_insets(
            MARGIN, GAP, TOP_HEIGHT, bottom_height, progress, InfoCard.WIDTH
        )
        self._canvas.set_insets(left, top, right, bottom)
        if self._video is not None:
            if self._immersive():
                self._video.set_insets(0, 0, 0, 0)
            else:
                self._video.set_insets(left, top, right, bottom + VIDEO_BAR_HEIGHT + GAP)
        self._sync_renderer()

    def _sync_renderer(self) -> None:
        if self._video is not None:
            self._video.set_gpu(not self._immersive())

    def _info_rect_limits(self) -> tuple[int, int]:
        top = MARGIN + TOP_HEIGHT + GAP
        reserved = MARGIN + (STRIP_HEIGHT if self._strip.isVisible() else BOTTOM_HEIGHT) + GAP
        return top, max(160, self.height() - top - reserved)

    def _place_info(self) -> None:
        top, available = self._info_rect_limits()
        height = min(self._info_height, available)
        # Docked on the left; slides in from the left edge towards its resting place.
        x = MARGIN - int((1.0 - self._info_progress) * 14)
        self._info.setGeometry(x, top, InfoCard.WIDTH, height)
        # The previous button rides along with the card so it never sits underneath it.
        shift = round(self._info_progress * (InfoCard.WIDTH + GAP))
        self._prev.move(MARGIN + shift, (self.height() - self._prev.height()) // 2)

    # // auto-hiding chrome

    def _start_idle(self) -> None:
        """(Re)arm the auto-hide countdown, unless the user turned auto-hide off in Settings."""
        if self._immersive():
            self._idle.start(IMMERSIVE_IDLE_MS)
        elif prefs().get("auto_hide_controls"):
            self._idle.start(IDLE_MS)
        else:
            self._idle.stop()

    def _on_pref_changed(self, key: str) -> None:
        if key == "hide_controls_on_zoom" and not prefs().get("hide_controls_on_zoom"):
            self._end_zoom_hide()
            return
        if key != "auto_hide_controls" or self._zoom_hidden:
            return
        if self._immersive() or prefs().get("auto_hide_controls"):
            self._start_idle()
        else:
            self._idle.stop()
            self._fader.fade_to(1.0)  # keep the controls visible from now on

    def _on_gesture_zoomed(self) -> None:
        """Optional: get the controls out of the way while the photo is zoomed with the wheel or a
        double-click. They stay hidden until the photo is back at its resting size; meanwhile the
        top and bottom edges of the window bring them back (see _follow_zoom_edges)."""
        if not prefs().get("hide_controls_on_zoom") or self._canvas.is_fit():
            return
        if self._zoom_hidden or self._menu_open or self._info.is_editing():
            return
        self._zoom_hidden = True
        self._idle.stop()
        self._fader.fade_to(0.0)
        self._video_bar.hide_volume()

    def _on_zoom_changed(self, _percent: float) -> None:
        if self._zoom_hidden and self._canvas.is_fit():
            self._end_zoom_hide()

    def _end_zoom_hide(self) -> None:
        if not self._zoom_hidden:
            return
        self._zoom_hidden = False
        self._start_idle()
        self._fader.fade_to(1.0)

    def _follow_zoom_edges(self, position: QPoint) -> None:
        """Zoomed with the controls hidden: ordinary mouse movement and panning leave them alone.
        Only the mouse near the top or bottom edge shows them, and they hide again once it leaves
        (unless it is on a control or a menu is open)."""
        local = self.mapFromGlobal(position)
        inside = not self.isMinimized() and self.rect().contains(local)
        at_edge = inside and (local.y() <= ZOOM_EDGE_PX or local.y() >= self.height() - ZOOM_EDGE_PX)
        if at_edge:
            self._fader.fade_to(1.0)
        elif self._fader.is_shown() and not self._chrome_busy():
            self._fader.fade_to(0.0)
            self._video_bar.hide_volume()

    def _poke(self) -> None:
        self._set_cursor_hidden(False)
        if self._zoom_hidden:
            return  # stays hidden until the mouse reaches an edge or the photo is back at fit
        self._start_idle()
        if not self._fader.is_shown():
            self._fader.fade_to(1.0)

    def _chrome_busy(self) -> bool:
        if self._menu_open or self._info.underMouse() or self._info.is_editing():
            return True
        if self._immersive():
            return bool(QApplication.mouseButtons())
        return self._video_bar.volume_popup().underMouse() or any(widget.underMouse() for widget in self._chrome)

    def _on_idle(self) -> None:
        if self._editor is not None:
            return  # the editor has its own controls; the cursor must stay visible
        immersive = self._immersive()
        if not immersive and not prefs().get("auto_hide_controls"):
            return
        if self._chrome_busy():
            self._idle.start(IMMERSIVE_IDLE_MS if immersive else IDLE_MS)
            return
        self._fader.fade_to(0.0)
        self._video_bar.hide_volume()
        if immersive:
            self._set_cursor_hidden(True)

    # // info card

    def _on_thumbnails_changed(self, *_args) -> None:
        self._strip.refresh()

    def _set_info(self, show: bool) -> None:
        if show == self._info_open:
            return
        self._info_open = show
        self._tools.set_checked("info", show)
        self._info_animation.stop()
        if show:
            self._refresh_info()
            self._info.show()
            self._info.raise_()
        self._info_animation.setStartValue(self._info_progress)
        self._info_animation.setEndValue(1.0 if show else 0.0)
        self._info_animation.start()
        self._relayout()

    def _refresh_info(self) -> None:
        if not self._items:
            return
        item = self._items[self._row]
        self._info.populate(item, self._current)
        self._info_height = self._info.desired_height()
        self._place_info()

    def _on_info_progress(self, value) -> None:
        self._info_progress = float(value)
        self._info_effect.setEnabled(self._info_progress < 0.999)
        self._info_effect.setOpacity(self._info_progress)
        self._place_info()
        self._apply_insets()

    def _on_info_finished(self) -> None:
        if not self._info_open:
            self._info.hide()
        self._apply_insets()

    # // navigation and loading

    def _is_favorite(self, item: MediaItem) -> bool:
        return bool(self._hooks.is_favorite(item)) if self._hooks.is_favorite else False

    def _update_nav(self) -> None:
        self._prev.setVisible(self._row > 0)
        self._next.setVisible(self._row < len(self._items) - 1)
        self._canvas.set_can_step(self._row > 0, self._row < len(self._items) - 1)
        self._update_skip()

    def _update_skip(self) -> None:
        if not self._items:
            return
        if self._items[self._row].is_video and self._play_mode in ("order", "shuffle"):
            self._video_bar.set_skip_enabled(self._can_go_video(-1), self._can_go_video(1))
        else:
            self._video_bar.set_skip_enabled(self._row > 0, self._row < len(self._items) - 1)

    def show_path(self, path: Path) -> bool:
        target = os.path.normcase(str(path))
        row = next((index for index, item in enumerate(self._items) if os.path.normcase(str(item.path)) == target), None)
        if row is None:
            return False
        self._show_row(row)
        return True

    def _show_row(self, row: int) -> None:
        if not self._items:
            QTimer.singleShot(0, self.close)
            return
        self._info.commit_edit()  # a half-typed name belongs to the photo being left
        self._flush_rotation()  # ...and so does a turn that is still waiting to be written
        count = len(self._items)
        row = max(0, min(row, count - 1))
        self._row = row
        item = self._items[row]
        if item.is_video and not self._auto_nav:
            self._start_shuffle_round(item)  # a video chosen by hand starts a new round
        self._title.set_texts(item.name, geo.subtitle(item.sort_time, item.width, item.height))
        self.setWindowTitle(f"{item.name}  ({row + 1}/{count})")
        self._counter.set_text(f"{row + 1:,} of {count:,}")
        self._strip.set_row(row)
        self._actions.set_checked("favorite", self._is_favorite(item))
        self._update_nav()
        self._relayout()

        # Videos are not decoded by the photo loader: it only prefetches the photos around the row.
        wanted = [self._items[r] for r in (row, row + 1, row - 1) if 0 <= r < count and not self._items[r].is_video]
        self._loader.want(wanted)
        if item.is_video:
            self._show_video(item)
        else:
            self._leave_video()
            data = self._loader.get(item)
            if data is not None:
                self._display(item, data)
            else:
                self._show_placeholder(item)
        if self._slideshow.isActive():
            self._slideshow.start()  # restart the interval after a manual step

    # // video

    # What does not apply to a video is greyed out, not removed.
    PHOTO_ONLY_TOOLS = ("print", "wallpaper")
    PHOTO_ONLY_ACTIONS = ("rotate", "edit")

    def _is_video(self) -> bool:
        item = self._current_item()
        return item is not None and item.is_video

    def _ensure_video(self) -> VideoSurface:
        if self._video is None:
            surface = VideoSurface(self)
            surface.caption_hit = self._is_caption_at
            surface.caption_pressed.connect(self._start_move)
            surface.caption_double_clicked.connect(self._toggle_maximized)
            surface.pressed.connect(self._reclaim_focus)
            surface.side_clicked.connect(self._step)
            surface.position_changed.connect(self._video_bar.set_position)
            surface.duration_changed.connect(self._video_bar.set_duration)
            surface.playing_changed.connect(self._video_bar.set_playing)
            surface.ended.connect(self._on_video_ended)
            surface.failed.connect(self._on_video_failed)
            surface.set_loop(self._play_mode == "repeat")
            surface.set_volume(self._volume / 100)
            surface.set_muted(self._muted)
            surface.setGeometry(self.rect())
            surface.stackUnder(self._message)  # above the photo canvas, below every floating control
            self._video = surface
            self._apply_insets()
        return self._video

    def _apply_media_mode(self, video: bool) -> None:
        self._video_mode = video
        for key in self.PHOTO_ONLY_TOOLS:
            self._tools.set_enabled(key, not video)
        for key in self.PHOTO_ONLY_ACTIONS:
            self._actions.set_enabled(key, not video)
        self._zoom.set_zoom_enabled(not video)
        self._video_bar.setVisible(video)
        self._relayout()

    def _show_video(self, item: MediaItem) -> None:
        surface = self._ensure_video()
        self._current = None
        self._message.hide()
        self._apply_media_mode(True)
        self._canvas.clear_image()
        self._canvas.hide()
        surface.show()
        self._video_bar.set_duration(int(item.duration * 1000))
        self._video_bar.set_position(0)
        self._video_bar.set_audio(self._volume / 100, self._muted)
        self._video_bar.set_play_mode(self._play_mode)
        self._title.set_texts(item.name, geo.subtitle(item.sort_time, item.width, item.height))
        self._arrange_top()
        surface.load(item.path)
        if self._info_open:
            self._refresh_info()

    def _leave_video(self) -> None:
        if not self._video_mode:
            return
        if self._video is not None:
            self._video.release()
            self._video.hide()
        self._canvas.show()
        self._apply_media_mode(False)

    def _toggle_video(self) -> None:
        if self._video is not None and self._video_mode:
            self._video.toggle_play()

    def _seek_video(self, ms: int) -> None:
        if self._video is not None and self._video_mode:
            self._video.seek_to(ms)

    def _toggle_mute(self) -> None:
        if self._muted or self._volume == 0:
            self._muted = False
            if self._volume == 0:
                self._volume = 50  # unmuting from an empty slider would stay silent
        else:
            self._muted = True
        self._apply_audio()

    def _on_volume(self, level: float) -> None:
        self._volume = round(level * 100)
        if self._volume > 0:
            self._muted = False  # moving the slider up is an unmute
        self._apply_audio()

    def _apply_audio(self) -> None:
        if self._video is not None:
            self._video.set_volume(self._volume / 100)
            self._video.set_muted(self._muted)
        self._video_bar.set_audio(self._volume / 100, self._muted)
        prefs().set("video_volume", self._volume)
        prefs().set("video_muted", self._muted)

    def _cycle_play_mode(self) -> None:
        self._play_mode = PLAY_MODES[(PLAY_MODES.index(self._play_mode) + 1) % len(PLAY_MODES)]
        prefs().set("video_play_mode", self._play_mode)
        if self._items and self._items[self._row].is_video:
            self._start_shuffle_round(self._items[self._row])
        if self._video is not None:
            self._video.set_loop(self._play_mode == "repeat")
        self._video_bar.set_play_mode(self._play_mode)
        self._update_skip()

    # The queue is the list the viewer was opened with (one folder, subfolders included); photos are skipped.

    @staticmethod
    def _key(item: MediaItem) -> str:
        return os.path.normcase(str(item.path))

    def _video_rows(self) -> list[int]:
        return [row for row, item in enumerate(self._items) if item.is_video]

    def _row_of_key(self, key: str) -> int | None:
        return next((row for row, item in enumerate(self._items) if self._key(item) == key), None)

    def _start_shuffle_round(self, item: MediaItem) -> None:
        key = self._key(item)
        self._shuffle_trail, self._shuffle_pos, self._shuffle_seen = [key], 0, {key}

    def _can_go_video(self, direction: int) -> bool:
        rows = self._video_rows()
        if self._play_mode == "order":
            return any((row > self._row) if direction > 0 else (row < self._row) for row in rows)
        if direction > 0:
            return self._shuffle_pos + 1 < len(self._shuffle_trail) or any(row != self._row for row in rows)
        return self._shuffle_pos > 0

    def _pick_video_row(self, direction: int) -> int | None:
        rows = self._video_rows()
        if self._play_mode == "order":
            ahead = [row for row in rows if (row > self._row) == (direction > 0) and row != self._row]
            return (min(ahead) if direction > 0 else max(ahead)) if ahead else None
        if direction < 0:  # back through the videos shuffle has played
            if self._shuffle_pos > 0:
                row = self._row_of_key(self._shuffle_trail[self._shuffle_pos - 1])
                if row is not None:
                    self._shuffle_pos -= 1
                    return row
            return None
        while self._shuffle_pos + 1 < len(self._shuffle_trail):  # forward again after going back
            row = self._row_of_key(self._shuffle_trail[self._shuffle_pos + 1])
            if row is not None:
                self._shuffle_pos += 1
                return row
            del self._shuffle_trail[self._shuffle_pos + 1]
        candidates = [row for row in rows if row != self._row]
        if not candidates:
            return None
        fresh = [row for row in candidates if self._key(self._items[row]) not in self._shuffle_seen]
        if not fresh:  # everything was played: a new round
            current = self._items[self._row]
            self._shuffle_seen = {self._key(current)} if current.is_video else set()
            fresh = candidates
        row = random.choice(fresh)
        key = self._key(self._items[row])
        self._shuffle_trail.append(key)
        self._shuffle_pos = len(self._shuffle_trail) - 1
        self._shuffle_seen.add(key)
        return row

    def _go_video(self, direction: int) -> bool:
        row = self._pick_video_row(direction)
        if row is None:
            return False
        self._auto_nav = True
        try:
            self._show_row(row)
        finally:
            self._auto_nav = False
        return True

    def _bar_step(self, delta: int) -> None:
        if self._video_mode and self._play_mode in ("order", "shuffle"):
            self._go_video(delta)
        else:
            self._step(delta)

    def _on_video_ended(self) -> None:
        if self._slideshow.isActive() and self._video_mode:
            self._advance()  # a slideshow waits for a clip to finish
        elif self._video_mode and self._play_mode in ("order", "shuffle"):
            self._go_video(1)  # the last video of the queue just ends

    def _on_video_failed(self, reason: str) -> None:
        if not self._video_mode:
            return
        self._message.setText(f"This video can't be played.\n{reason}")
        self._message.show()

    def _release_video_file(self) -> bool:
        """Close the clip so the file can be renamed / deleted. True when a video was open."""
        if self._video_mode and self._video is not None:
            self._video.release()
            return True
        return False

    def _show_placeholder(self, item: MediaItem) -> None:
        self._current = None
        self._message.hide()
        thumb = self._hooks.thumbnail_for(item) if self._hooks.thumbnail_for else None
        if thumb is not None and not thumb.isNull():
            self._canvas.show_preview(thumb, item.width, item.height)
        else:
            self._canvas.clear_image()
        if self._info_open:
            self._refresh_info()

    def _display(self, item: MediaItem, data: ViewData) -> None:
        self._current = data
        self._message.hide()
        self._canvas.set_image(data.image)
        if data.animation is not None:
            self._canvas.play_animation(data.animation)
        self._title.set_texts(item.name, geo.subtitle(data.taken_at or item.sort_time, data.width, data.height))
        self._arrange_top()
        if self._info_open:
            self._refresh_info()

    def _on_loaded(self, key: str, data: ViewData) -> None:
        if self._items and key == str(self._items[self._row].path):
            self._display(self._items[self._row], data)

    def _on_failed(self, key: str, reason: str) -> None:
        if not self._items or key != str(self._items[self._row].path):
            return
        self._current = None
        self._canvas.clear_image()
        self._message.setText(f"This photo can't be opened.\n{reason}")
        self._message.show()
        if self._info_open:
            self._refresh_info()

    def _reclaim_focus(self) -> None:
        """Hand the keyboard back to the window, saving a name that is being edited."""
        self._info.commit_edit()
        self.setFocus()

    def _on_rename(self, stem: str) -> None:
        item = self._current_item()
        hook = self._hooks.rename
        if item is None or hook is None:
            return
        self._flush_rotation()
        item = self._current_item()
        self._idle.stop()
        reopen = self._release_video_file()
        try:
            renamed = hook(item, stem, self)
        finally:
            self._poke()
        if renamed is None or renamed.path == item.path:
            if reopen:
                self._show_row(self._row)
            return
        self._loader.renamed(item, renamed)
        self._items[self._row] = renamed
        self._strip.set_items(self._items, self._row)
        self._show_row(self._row)

    def _step(self, delta: int) -> None:
        row = self._row + delta
        if 0 <= row < len(self._items):
            self._show_row(row)

    def _advance(self, from_timer: bool = False) -> None:
        if self._video_mode and from_timer:
            return  # a video moves on when it ends, not when the photo interval runs out
        if self._items:
            self._show_row((self._row + 1) % len(self._items))

    # // actions

    def _current_item(self) -> MediaItem | None:
        return self._items[self._row] if self._items else None

    def _on_toolbar(self, key: str) -> None:
        item = self._current_item()
        if item is None:
            return
        if item.is_video and key in self.PHOTO_ONLY_TOOLS + self.PHOTO_ONLY_ACTIONS:
            return  # greyed out for a video
        if key == "edit":
            self._open_editor(item)
        elif key == "rotate":
            self._rotate()
        elif key == "favorite":
            self._toggle_favorite()
        elif key == "copy":
            self._copy()
        elif key == "print":
            self._print()
        elif key == "wallpaper":
            self._set_wallpaper()
        elif key == "slideshow":
            self._toggle_slideshow(from_toolbar=True)
        elif key == "delete":
            self._delete()
        elif key == "more":
            self._show_more_menu()
        elif key == "info":
            self._set_info(not self._info_open)

    # // editing the file

    def _open_editor(self, item: MediaItem) -> None:
        if self._editor is not None or item.is_video:
            return
        if self._hooks.save_edit is None:
            if self._hooks.edit:
                self._hooks.edit(item)
            return
        from ..editor import EditorView  # imported late: the editor itself imports the viewer's style

        self._flush_rotation()  # the file must be as the photo shows before it is edited
        if self._slideshow.isActive():
            self._toggle_slideshow()
        self._set_cursor_hidden(False)
        editor = EditorView(item, self._hooks.save_edit, self)
        editor.finished.connect(self._editor_finished)
        self._editor = editor
        editor.setGeometry(self.rect())
        editor.show()
        editor.raise_()
        for grip, _edges in getattr(self, "_grips", ()):  # the window edges stay usable for resizing
            grip.raise_()
        editor.setFocus()

    def _editor_finished(self, renewed) -> None:
        editor, self._editor = self._editor, None
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        if renewed is not None:
            self._apply_edit(renewed)
        self._reclaim_focus()
        self._poke()

    def _apply_edit(self, renewed: MediaItem) -> None:
        """Show the result of an edit: the same photo again (replaced) or the new copy next to it."""
        old = self._current_item()
        if old is None:
            return
        row = self._row
        if renewed.path == old.path:
            self._loader.forget(old)
            self._items[row] = renewed
            self._strip.set_items(self._items, row)
            self._show_row(row)
            return
        self._items = self._items[: row + 1] + [renewed] + self._items[row + 1 :]
        self._strip.set_items(self._items, row + 1)
        self._show_row(row + 1)

    # // rotating the file

    def _rotate(self, degrees: int = 90) -> None:
        """Turn the photo clockwise: the view at once, the file once the clicking has settled."""
        item = self._current_item()
        if item is None or item.is_video or not self._canvas.has_image():
            return
        self._canvas.rotate_view(degrees)
        if self._hooks.rotate is None or (self._current is not None and self._current.animation is not None):
            return  # an animated GIF cannot be rewritten rotated: only the view turns
        self._rot_item = item
        self._rot_degrees = (self._rot_degrees + degrees) % 360
        if self._rot_degrees == 0:  # a full turn: nothing to write
            self._rot_item = None
            self._rot_timer.stop()
        else:
            self._rot_timer.start()

    def _flush_rotation(self) -> None:
        """Write a pending rotation to the file now. Safe to call at any time."""
        self._rot_timer.stop()
        item, degrees = self._rot_item, self._rot_degrees
        self._rot_item, self._rot_degrees = None, 0
        hook = self._hooks.rotate
        if item is None or hook is None or degrees % 360 == 0:
            return
        self._idle.stop()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            renewed = hook(item, degrees, self)
        finally:
            QApplication.restoreOverrideCursor()
            self._poke()
        index = next((i for i, entry in enumerate(self._items) if entry.path == item.path), None)
        if index is None:
            return
        if renewed is None:  # the file could not be rotated: put the view back as it was
            if index == self._row:
                self._canvas.rotate_view(-degrees)
            return
        self._loader.forget(self._items[index])
        self._items[index] = renewed
        self._strip.set_items(self._items, self._row)
        if index == self._row:
            # The canvas already shows the turned photo; reload it quietly (no placeholder, which
            # would flash the old thumbnail) so size, Info and the cache describe the new file.
            count = len(self._items)
            wanted = [self._items[self._row]] + [self._items[r] for r in (self._row + 1, self._row - 1) if 0 <= r < count]
            self._loader.want(wanted)

    def _toggle_zoom(self) -> None:
        if self._canvas.is_fit():
            self._canvas.actual_size()
        else:
            self._canvas.fit()

    def _toggle_favorite(self) -> None:
        item = self._current_item()
        if item is None or self._hooks.toggle_favorite is None:
            return
        self._hooks.toggle_favorite(item)
        self._actions.set_checked("favorite", self._is_favorite(item))

    def _toggle_slideshow(self, from_toolbar: bool = False) -> None:
        if self._slideshow.isActive():
            self._slideshow.stop()
        else:
            self._slideshow.start()
            if from_toolbar and not self.isFullScreen():
                self._toggle_fullscreen()
        self._tools.set_checked("slideshow", self._slideshow.isActive())

    def _copy(self) -> None:
        item = self._current_item()
        if item is None:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(item.path))])
        if self._current is not None:
            mime.setImageData(self._current.image)
        QApplication.clipboard().setMimeData(mime)

    def _copy_path(self) -> None:
        item = self._current_item()
        if item is not None:
            QApplication.clipboard().setText(f'"{item.path}"')

    def _reveal(self) -> None:
        item = self._current_item()
        if item is None:
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(f'explorer /select,"{item.path}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(item.path)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.path.parent)))
        except OSError:
            pass

    def _print(self) -> None:
        item = self._current_item()
        if self._current is None or item is None or self._video_mode:
            return
        image = self._current.image
        if self._rot_item is not None and self._rot_item.path == item.path and self._rot_degrees % 360:
            # a turn that is only on screen so far (it is written to the file a moment later) is printed too
            image = image.transformed(QTransform().rotate(self._rot_degrees))
        try:
            from .print_dialog import PrintDialog

            dialog = PrintDialog(image, item.name, self)
        except Exception as error:  # never leave a click on Print without an answer
            traceback.print_exc()
            warn(self, "Print", f"The print preview could not be opened ({type(error).__name__}: {error}).\nThe system print dialog is used instead.")
            self._print_native(image)
            return
        dialog.exec()
        dialog.deleteLater()

    def _print_native(self, image) -> None:
        """The system's own print dialog: no preview, but it works with every printer."""
        try:
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except ImportError:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() not in (1, QDialog.DialogCode.Accepted):
            return
        painter = QPainter(printer)
        area = painter.viewport()
        size = image.size()
        size.scale(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        painter.setViewport(area.x(), area.y(), size.width(), size.height())
        painter.setWindow(image.rect())
        painter.drawImage(0, 0, image)
        painter.end()

    def _delete(self) -> None:
        self._flush_rotation()
        item = self._current_item()
        if item is None or self._hooks.delete is None:
            return
        self._idle.stop()
        reopen = self._release_video_file()
        removed = self._hooks.delete(item, self)
        self._poke()
        if not removed:
            if reopen:
                self._show_row(self._row)
            return
        self._loader.forget(item)
        del self._items[self._row]
        if not self._items:
            self.close()
            return
        row = min(self._row, len(self._items) - 1)
        self._strip.set_items(self._items, row)
        self._show_row(row)

    def _set_sidebar_background(self) -> None:
        item = self._current_item()
        hook = self._hooks.set_sidebar_background
        if item is not None and hook is not None:
            QTimer.singleShot(0, lambda: hook(item))  # let this menu finish closing before a dialog opens

    def _set_wallpaper(self) -> None:
        item = self._current_item()
        hook = self._hooks.set_wallpaper
        if item is not None and not item.is_video and hook is not None:
            QTimer.singleShot(0, lambda: hook(item, self))  # let a menu finish closing before any dialog opens

    def _show_more_menu(self) -> None:
        try:
            menu = AnimatedMenu(self)
        except Exception:
            menu = QMenu(self)
        menu.setProperty("floatMenu", True)  # follows the "Floating panel opacity" setting (see theme.stylesheet)
        collapsed = {
            "rotate": ("Rotate", self._rotate),
            "favorite": ("Favorite", self._toggle_favorite),
            "copy": ("Copy photo", self._copy),
            "print": ("Print...", self._print),
            "wallpaper": ("Set as wallpaper", self._set_wallpaper),
            "slideshow": ("Slideshow", lambda: self._toggle_slideshow(from_toolbar=True)),
        }
        hidden = self._hidden_keys
        video = self._video_mode
        photo_only = set(self.PHOTO_ONLY_TOOLS + self.PHOTO_ONLY_ACTIONS)
        if video:
            collapsed["copy"] = ("Copy video", self._copy)
        for key in ("rotate", "favorite", "copy", "print", "wallpaper", "slideshow"):
            if key in hidden:
                label, handler = collapsed[key]
                action = menu.addAction(label, handler)
                action.setEnabled(not (video and key in photo_only))
        if hidden:
            menu.addSeparator()
        menu.addAction("Copy file path", self._copy_path)
        menu.addAction("Show in folder", self._reveal)
        if self._hooks.set_sidebar_background is not None:
            menu.addAction("Set as sidebar background", self._set_sidebar_background).setEnabled(not video)
        anchor = self._tools.button("more")
        origin = anchor.mapToGlobal(QPoint(anchor.width() - menu.sizeHint().width(), anchor.height() + 10))
        self._menu_open = True
        try:
            menu.exec(origin)
        finally:
            self._menu_open = False
            menu.deleteLater()
            self._poke()

    # // keyboard

    def keyPressEvent(self, event) -> None:
        if self._info.is_editing():  # the keyboard belongs to the name, never to the shortcuts
            self._info.forward_key(event)
            return
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_C:
            self._copy()
        elif ctrl and key == Qt.Key.Key_P:
            self._print()
        elif ctrl and key == Qt.Key.Key_B:
            self._set_wallpaper()  # ignores a video and does nothing where wallpapers are not supported
        elif key == Qt.Key.Key_Space and self._video_mode:
            self._toggle_video()  # Space plays / pauses a video instead of stepping on
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._video_mode and self._video is not None:
            # In a video the left / right arrows skip back / forward (5 or 10 s, Settings > Videos);
            # Up / Down / PageUp / PageDown still go to the previous / next item.
            step = self._video.seek_step_ms()
            self._video.seek_by(step if key == Qt.Key.Key_Right else -step)
            self._poke()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self._step(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self._step(-1)
        elif key == Qt.Key.Key_Home:
            self._show_row(0)
        elif key == Qt.Key.Key_End:
            self._show_row(len(self._items) - 1)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._canvas.zoom_smooth(ZOOM_FACTOR)
        elif key == Qt.Key.Key_Minus:
            self._canvas.zoom_smooth(1 / ZOOM_FACTOR)
        elif key == Qt.Key.Key_0:
            self._canvas.fit(upscale=True)
        elif key == Qt.Key.Key_F:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_1:
            self._canvas.actual_size()
        elif key == Qt.Key.Key_R:
            self._rotate()
        elif key == Qt.Key.Key_F2 and self._hooks.rename is not None:
            self._set_info(True)
            self._info.focus_name()
        elif key == Qt.Key.Key_Delete:
            self._delete()
        elif key == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self._toggle_fullscreen()
            else:
                self.close()
        elif key == Qt.Key.Key_S:
            self._toggle_slideshow()
        elif key == Qt.Key.Key_I:
            self._set_info(not self._info_open)
        else:
            super().keyPressEvent(event)