import time
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from math import ceil, exp

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QImageReader,
    QMovie,
    QColor,
    QContextMenuEvent,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QStaticText,
    QTransform,
)
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QFrame, QWidget

from ... import profiling
from ...config import DEFAULT_ROW_HEIGHT, MAX_ROW_HEIGHT, MIN_ROW_HEIGHT, TILE_GAP
from ...formatting import format_duration
from .model import GalleryModel
from ..backdrop import backdrop
from ..common.icons import draw_icon
from .layout import justify_rows, square_rows
from ..common.motion import (
    DURATION_NORMAL,
    DURATION_SLOW,
    SCROLL_ANIMATION_MS,
    WHEEL_PIXELS_PER_NOTCH,
    WheelInput,
    make_animation,
    standard,
)
from ..theme import theme
from ..theme.preferences import prefs
from .rail import LABEL_GAP, Rail, RailStop, build_rail

MARGIN_LEFT = 8
HEADER_INSET = 3
TIMELINE_WIDTH = 40
TIMELINE_MARGIN = 6
TIMELINE_GAP = 6
TIMELINE_DOT_RADIUS = 1.5  # 3px dots, same as Windows Photos
TIMELINE_DOT_OFFSET = 8.0  # dots sit right of the label centre
TIMELINE_MARKER_WIDTH = 26.0
TIMELINE_LABEL_GAP = 1.0  # year labels sit just above their boundary, the position marker right below
TIMELINE_DOT_MERGE = 5.0  # px: closer dots are drawn as a single dot
TIMELINE_SNAP = 7.0  # px: pressing this close to a dot jumps straight to its bucket
# The year rail owns its own column at the right edge, so photos never run underneath it.
MARGIN_RIGHT = TIMELINE_WIDTH + TIMELINE_MARGIN + TIMELINE_GAP
TIMELINE_TOP = 26
TIMELINE_BOTTOM = 14
TIMELINE_END_PADDING = 24
TOP_PADDING = 14
BOTTOM_PADDING = 32
HEADER_HEIGHT = 36
GROUP_SPACING = 0
WHEEL_ZOOM_STEP = 16
FADE_SECONDS = 0.22
DEFAULT_REFRESH_HZ = 60.0
MAX_REFRESH_HZ = 240.0
SCROLL_IDLE_SECONDS = 0.25
TRANSITION_SNAPSHOT_TTL = 0.5
TILE_BUILD_BUDGET_SECONDS = 0.004
WANTED_REACH = 10
SMOOTH_TAU_SECONDS = 0.07
TILE_CACHE_BYTE_LIMIT = 160 * 1024 * 1024
SCROLL_STEP = 48
RADIUS = 12.0
CHECK_SIZE = 24
CHECK_INSET = 8
HEART_SIZE = 24
BADGE_WIDTH = 34.0
BADGE_HEIGHT = 18.0
BADGE_PADDING = 8.0
BORDER_WIDTH = 2.0
VIDEO_PLAY_MIN_SIDE = 72  # smaller tiles get no play button in the middle
VIDEO_DURATION_MIN_WIDTH = 64  # narrower tiles get no length label
VIDEO_FORMAT_MIN_WIDTH = 132  # narrower video tiles drop the format badge so it never meets the length label
GROUP_BUTTON_HEIGHT = 24
GROUP_BUTTON_PAD = 12
GROUP_BUTTON_GAP = 14

SHADE_HEIGHT = 56
SHADE_ALPHA = 90
BADGE_BACKGROUND = QColor(0, 0, 0, 150)


def _tone(role: str) -> QColor:
    """A color from the active theme's palette, looked up at paint time so theme switches apply live."""
    return QColor(getattr(theme.palette(), role))


TIP_DELAY_MS = 500
TIP_TOLERANCE_PX = 6


class _TileTip(QWidget):
    """Small rounded hover label with the file name; hugs the text with thin padding."""

    PAD_X = 9
    PAD_Y = 4
    RADIUS = 6.0

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""

    def show_text(self, text: str, global_pos: QPoint) -> None:
        self._text = text
        self.setFont(QApplication.font())
        metrics = self.fontMetrics()
        self.resize(
            metrics.horizontalAdvance(text) + 2 * self.PAD_X + 2, metrics.height() + 2 * self.PAD_Y + 2
        )
        screen = QApplication.screenAt(global_pos)
        x, y = global_pos.x() + 12, global_pos.y() + 20
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - self.width()))
            if y + self.height() > area.bottom():
                y = global_pos.y() - self.height() - 6
        self.move(x, y)
        self.show()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(_tone("pill_border"), 1.0))
        painter.setBrush(theme.float_color("pill_bg"))
        painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        painter.setPen(_tone("pill_text"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


@dataclass
class _Row:
    y: int
    height: int
    tiles: list[tuple[int, int, int]]


@dataclass
class _Flip:
    """Tiles gliding from their old layout to the new one while the view changes width."""

    old_rects: dict[int, QRect]  # viewport coordinates of the tiles that were on screen
    old_headers: dict[str, int]  # group header label -> viewport y
    old_width: int
    new_width: int


def _format_year(timestamp: float) -> str:
    try:
        return str(datetime.fromtimestamp(timestamp).year)
    except (OverflowError, OSError, ValueError):
        return ""


def _format_day(timestamp: float) -> str:
    try:
        moment = datetime.fromtimestamp(timestamp)
        return f"{moment:%B} {moment.day}, {moment.year}"
    except (OverflowError, OSError, ValueError):
        return "Unknown date"


def _day_of(timestamp: float) -> date | None:
    try:
        return datetime.fromtimestamp(timestamp).date()
    except (OverflowError, OSError, ValueError):
        return None


def _format_group_day(day: date | None, today: date) -> str:
    if day is None:
        return "Unknown date"
    delta = (today - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if day.year == today.year:
        return f"{day:%a}, {day.day} {day:%b}"
    return f"{day.day} {day:%b} {day.year}"


def _format_month_year(timestamp: float) -> str:
    try:
        moment = datetime.fromtimestamp(timestamp)
        return f"{moment:%B} {moment.year}"
    except (OverflowError, OSError, ValueError):
        return ""


class GalleryView(QAbstractScrollArea):
    activated = Signal(int)
    row_height_changed = Signal(int)
    selection_changed = Signal()
    context_menu_requested = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model: GalleryModel | None = None
        self._row_height = DEFAULT_ROW_HEIGHT
        self._square = False
        self._tip = _TileTip()
        self._tip_index: int | None = None
        self._tip_pending: int | None = None
        self._tip_anchor = QPoint()
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(TIP_DELAY_MS)
        self._tip_timer.timeout.connect(self._show_tip)
        self._rows: list[_Row] = []
        self._row_tops: list[int] = []
        self._row_of: list[int] = []
        self._rects: list[QRect] = []
        self._headers: list[tuple[int, str, int]] = []
        self._header_tops: list[int] = []
        self._header_ranges: list[tuple[int, int]] = []
        self._group_pressed: int | None = None
        self._rail_source: list[tuple[float, float]] = []
        self._rail: Rail | None = None
        self._rail_key: tuple | None = None
        self._content_height = 0
        self._selected: set[int] = set()
        self._select_mode = False
        self._hover: int | None = None
        self._context_target: int | None = None
        self._current: int | None = None
        self._anchor: int | None = None
        self._pressed: int | None = None
        self._keyboard = False
        self._empty_text = ""

        self._timeline_hovered = False
        self._timeline_dragging = False
        self._timeline_pointer_y = 0

        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(150)
        self._relayout_timer.timeout.connect(self._deferred_relayout)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.viewport().setAutoFillBackground(False)
        self.verticalScrollBar().setSingleStep(SCROLL_STEP)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._fading_rects: list[QRect] = []
        self._tiles: OrderedDict[str, tuple[int, QSize, float, QPixmap]] = OrderedDict()
        self._tile_bytes = 0
        self._badges: dict[str, tuple[float, QPixmap]] = {}
        self._duplicates: set[str] = set()  # paths of photos that are copies of another photo
        self._duplicate_badge: tuple[float, QPixmap] | None = None
        self._fonts: dict[str, QFont] = {}
        self._year_text: dict[str, QStaticText] = {}
        self._last_scroll = 0.0
        self._flip: _Flip | None = None
        self._layout_view_width = 0
        self._flip_timer = QTimer(self)
        self._flip_timer.setSingleShot(True)
        self._flip_timer.timeout.connect(self._finish_flip)
        self._pending_snapshot: tuple[QPixmap, float] | None = None
        self._xfade_pixmap: QPixmap | None = None
        self._xfade_progress = 1.0
        self._xfade = make_animation(self, DURATION_NORMAL, standard())
        self._xfade.valueChanged.connect(self._on_xfade_step)
        self._xfade.finished.connect(self._clear_crossfade)
        self._scroll_target = 0
        self._wheel_input = WheelInput()
        self._repaint_after_scroll = False  # tiles were left grey mid-glide; repaint when it ends
        self._scroll_from = 0
        self._scroll_started = 0.0
        self._scroll_smooth = False
        self._scroll_pos = 0.0
        self._scroll_last = 0
        self._scroll_clock = 0.0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.timeout.connect(self._scroll_tick)
        # Animated GIF tiles (Settings > Photos > Gallery): one QMovie per playing tile, its current frame
        # (already scaled to the tile) takes the place of the still thumbnail while it plays.
        self._gif_movies: dict[str, QMovie] = {}
        self._gif_frames: dict[str, QPixmap] = {}
        self._gif_index: dict[str, int] = {}
        self._gif_animated: dict[str, bool] = {}
        self._gif_timer = QTimer(self)
        self._gif_timer.setSingleShot(True)
        self._gif_timer.setInterval(80)  # a scroll moves many tiles: sync once it settles
        self._gif_timer.timeout.connect(self._sync_gifs)
        prefs().changed.connect(self._on_gif_preference)

    def setModel(self, model: GalleryModel) -> None:
        self._model = model
        model.modelReset.connect(self._on_reset)
        model.dataChanged.connect(self._on_data_changed)
        model.aspects_learned.connect(self._schedule_relayout)
        self._on_reset()

    def row_height(self) -> int:
        return self._row_height

    def square(self) -> bool:
        return self._square

    def set_square(self, square: bool) -> None:
        if square == self._square:
            return
        self._square = square
        self._relayout(keep_scroll=True)

    def set_row_height(self, height: int) -> None:
        height = max(MIN_ROW_HEIGHT, min(MAX_ROW_HEIGHT, int(height)))
        if height == self._row_height:
            return
        self._row_height = height
        self._relayout(keep_scroll=True)

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.viewport().update()

    def is_selecting(self) -> bool:
        return self._select_mode or bool(self._selected)

    def selected_rows(self) -> list[int]:
        return sorted(self._selected)

    def current_row(self) -> int | None:
        count = len(self._rects)
        if self._current is not None and 0 <= self._current < count:
            return self._current
        if self._hover is not None and 0 <= self._hover < count:
            return self._hover
        return None

    def set_context_target(self, row: int | None) -> None:
        if self._context_target == row:
            return
        previous, self._context_target = self._context_target, row
        for target in (previous, row):
            if target is not None and 0 <= target < len(self._rects):
                self.viewport().update(self._viewport_rect(target).adjusted(-2, -2, 2, 2))

    def focus_row(self, row: int) -> None:
        if not 0 <= row < len(self._rects):
            return
        self._current = row
        self._keyboard = True
        self.ensure_visible(row)
        self.viewport().update()

    def set_select_mode(self, enabled: bool) -> None:
        if enabled == self._select_mode:
            return
        self._select_mode = enabled
        if not enabled:
            self._selected.clear()
        self.viewport().update()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        self._select_mode = False
        if self._selected:
            self._selected.clear()
        self.viewport().update()
        self.selection_changed.emit()

    def select_all(self) -> None:
        if self._model is None:
            return
        self._selected = set(range(self._model.rowCount()))
        self.viewport().update()
        self.selection_changed.emit()

    # layout

    def begin_transition(self) -> None:
        """Call right before swapping what the gallery shows (folder, sort, filter).

        The next full reset then cross-fades from what is on screen now to the new content.
        """
        self._pending_snapshot = None
        if self._model is None or not self._rows or not self.isVisible():
            return
        if self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return
        self._pending_snapshot = (self.viewport().grab(), time.perf_counter())

    def _start_crossfade(self) -> None:
        pending, self._pending_snapshot = self._pending_snapshot, None
        if pending is None or time.perf_counter() - pending[1] > TRANSITION_SNAPSHOT_TTL:
            return
        self._xfade.stop()
        self._xfade_pixmap = pending[0]
        self._xfade_progress = 0.0
        self._xfade.start()
        self.viewport().update()

    def _on_xfade_step(self, value) -> None:
        self._xfade_progress = max(0.0, min(1.0, float(value)))
        self.viewport().update()

    def _clear_crossfade(self) -> None:
        self._xfade.stop()
        self._xfade_pixmap = None
        self._xfade_progress = 1.0
        self.viewport().update()

    def _paint_crossfade(self, painter: QPainter) -> None:
        if self._xfade_pixmap is None:
            return
        painter.setOpacity(max(0.0, 1.0 - self._xfade_progress))
        painter.drawPixmap(0, 0, self._xfade_pixmap)
        painter.setOpacity(1.0)

    def _schedule_relayout(self) -> None:
        self._relayout_timer.start()

    def _deferred_relayout(self) -> None:
        if self.is_scrolling():
            self._relayout_timer.start(int(SCROLL_IDLE_SECONDS * 1000))
            return
        self._relayout(keep_scroll=True)

    def is_scrolling(self) -> bool:
        return self._scroll_timer.isActive() or self._timeline_dragging or self._flip is not None or (
            time.perf_counter() - self._last_scroll < SCROLL_IDLE_SECONDS
        )

    def _frame_interval_ms(self) -> int:
        screen = self.screen()
        rate = float(screen.refreshRate()) if screen is not None else DEFAULT_REFRESH_HZ
        if rate < 30.0:
            rate = DEFAULT_REFRESH_HZ
        rate = min(rate, MAX_REFRESH_HZ)
        return max(4, int(round(1000.0 / rate)))

    def _scroll_running(self) -> bool:
        return self._scroll_timer.isActive()

    def _stop_scroll(self) -> None:
        self._scroll_timer.stop()
        self._flush_deferred_repaint()

    def _flush_deferred_repaint(self) -> None:
        if self._repaint_after_scroll:
            self._repaint_after_scroll = False
            self.viewport().update()

    def _start_scroll(self, target: int) -> None:
        bar = self.verticalScrollBar()
        self._scroll_smooth = False
        self._scroll_from = bar.value()
        self._scroll_target = target
        self._scroll_started = time.perf_counter()
        self._scroll_timer.start(self._frame_interval_ms())

    def _start_smooth(self, delta: float) -> None:
        bar = self.verticalScrollBar()
        if self._scroll_running() and self._scroll_smooth and bar.value() == self._scroll_last:
            base = self._scroll_target
        else:
            base = bar.value()
            self._scroll_pos = float(base)
        self._scroll_target = int(max(bar.minimum(), min(bar.maximum(), base - delta)))
        self._scroll_smooth = True
        self._scroll_last = bar.value()
        if not self._scroll_timer.isActive():
            self._scroll_clock = time.perf_counter()
            self._scroll_timer.start(self._frame_interval_ms())

    def _smooth_tick(self) -> None:
        bar = self.verticalScrollBar()
        if bar.value() != self._scroll_last:
            self._scroll_timer.stop()
            self._flush_deferred_repaint()
            return
        now = time.perf_counter()
        dt = min(0.05, now - self._scroll_clock)
        self._scroll_clock = now
        remaining = self._scroll_target - self._scroll_pos
        if abs(remaining) < 0.5:
            self._scroll_timer.stop()
            bar.setValue(self._scroll_target)
            self._scroll_last = bar.value()
            self._flush_deferred_repaint()
            return
        self._scroll_pos += remaining * (1.0 - exp(-dt / SMOOTH_TAU_SECONDS))
        bar.setValue(int(round(self._scroll_pos)))
        self._scroll_last = bar.value()

    def _scroll_tick(self) -> None:
        if self._scroll_smooth:
            self._smooth_tick()
            return
        bar = self.verticalScrollBar()
        elapsed = time.perf_counter() - self._scroll_started
        progress = elapsed * 1000.0 / SCROLL_ANIMATION_MS
        if progress >= 1.0:
            self._scroll_timer.stop()
            bar.setValue(self._scroll_target)
            self._flush_deferred_repaint()
            return
        eased = 1.0 - (1.0 - progress) ** 3
        value = self._scroll_from + (self._scroll_target - self._scroll_from) * eased
        bar.setValue(int(round(value)))

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange:
            self._gif_timer.start()  # play only while the window is in front
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange):
            self._fonts.clear()
            self._year_text.clear()
            self._badges.clear()
            self._duplicate_badge = None
        super().changeEvent(event)

    def _font_variant(self, name: str) -> QFont:
        font = self._fonts.get(name)
        if font is not None:
            return font
        font = QFont(self.font())
        if name == "header":
            font.setPointSizeF(font.pointSizeF() + 2.0)
            font.setWeight(QFont.Weight.Bold)
        elif name == "count":
            font.setPointSizeF(font.pointSizeF() - 0.5)
            font.setWeight(QFont.Weight.Medium)
        elif name == "year":
            font.setPointSizeF(8.0)
            font.setWeight(QFont.Weight.DemiBold)
        elif name == "pill":
            font.setPointSizeF(9.0)
            font.setWeight(QFont.Weight.DemiBold)
        elif name == "badge":
            font.setPointSize(8)
            font.setBold(True)
        self._fonts[name] = font
        return font

    def _year_static(self, year: str) -> QStaticText:
        text = self._year_text.get(year)
        if text is None:
            text = QStaticText(year)
            text.setPerformanceHint(QStaticText.PerformanceHint.AggressiveCaching)
            text.prepare(QTransform(), self._font_variant("year"))
            self._year_text[year] = text
        return text

    # // animated GIF tiles

    @staticmethod
    def _is_gif(item) -> bool:
        return not item.is_video and (item.format_label.upper() == "GIF" or item.path.suffix.lower() == ".gif")

    def _on_gif_preference(self, key: str) -> None:
        if key == "gif_autoplay":
            self._sync_gifs()

    def _gif_wanted(self) -> list[int]:
        """Indexes of the GIF tiles that should be moving now."""
        mode = str(prefs().get("gif_autoplay"))
        window = self.window()
        if (
            mode == "never"
            or self._model is None
            or not self._rows
            or not self.isVisible()
            or window.isMinimized()
            or not window.isActiveWindow()
        ):
            return []
        if mode == "hover":
            hovered = self._hover
            return [hovered] if hovered is not None and self._is_gif(self._model.item_at(hovered)) else []
        first, last = self._rows_span(self.verticalScrollBar().value())
        indexes = []
        for row in self._rows[first : last + 1]:
            indexes.extend(index for index, _x, _width in row.tiles if self._is_gif(self._model.item_at(index)))
        return indexes

    def _sync_gifs(self) -> None:
        wanted = {}
        for index in self._gif_wanted():
            wanted[str(self._model.item_at(index).path)] = index
        for key in [key for key in self._gif_movies if key not in wanted]:
            self._stop_gif(key)
        for key, index in wanted.items():
            self._gif_index[key] = index
            if key not in self._gif_movies:
                self._start_gif(key, index)

    def _start_gif(self, key: str, index: int) -> None:
        if index >= len(self._rects):
            return
        animated = self._gif_animated.get(key)
        if animated is None:
            reader = QImageReader(key)
            animated = reader.canRead() and reader.imageCount() > 1
            self._gif_animated[key] = animated
        if not animated:
            return
        reader = QImageReader(key)
        original = reader.size()
        if not original.isValid() or original.isEmpty():
            return
        ratio = self.devicePixelRatioF()
        rect = self._rects[index]
        scale = max(rect.width() / original.width(), rect.height() / original.height()) * ratio
        movie = QMovie(key, parent=self)
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        movie.setScaledSize(QSize(max(1, round(original.width() * scale)), max(1, round(original.height() * scale))))
        if not movie.isValid():
            movie.deleteLater()
            return
        movie.frameChanged.connect(lambda _frame, key=key: self._on_gif_frame(key))
        self._gif_movies[key] = movie
        movie.start()

    def _on_gif_frame(self, key: str) -> None:
        movie = self._gif_movies.get(key)
        index = self._gif_index.get(key)
        if movie is None or index is None or index >= len(self._rects):
            return
        self._gif_frames[key] = movie.currentPixmap()
        self.viewport().update(self._viewport_rect(index).adjusted(-1, -1, 1, 1))

    def _stop_gif(self, key: str) -> None:
        movie = self._gif_movies.pop(key, None)
        frame = self._gif_frames.pop(key, None)
        index = self._gif_index.pop(key, None)
        if movie is not None:
            movie.stop()
            movie.deleteLater()
        if frame is not None and index is not None and index < len(self._rects):
            self.viewport().update(self._viewport_rect(index).adjusted(-1, -1, 1, 1))  # back to the still

    def _stop_all_gifs(self) -> None:
        for key in list(self._gif_movies):
            self._stop_gif(key)

    def _on_scrolled(self, _value: int) -> None:
        if self._gif_movies or prefs().get("gif_autoplay") == "always":
            self._gif_timer.start()
        self._last_scroll = time.perf_counter()
        if self.viewport().underMouse() and not self._timeline_dragging:
            pos = self.viewport().mapFromGlobal(QCursor.pos())
            self._hover = None if self._in_timeline(pos) else self._index_at(pos)
            if prefs().get("gif_autoplay") == "hover":
                self._gif_timer.start()
        self.viewport().update()

    def _on_reset(self) -> None:
        keep_scroll = self._model is not None and self._model.take_soft_reset()
        self._selected.clear()
        self._hover = None
        self._context_target = None
        self._current = None
        self._anchor = None
        self._tiles.clear()
        self._tile_bytes = 0
        self._year_text.clear()
        self._wanted_span = None
        self._stop_all_gifs()
        self._relayout(keep_scroll=keep_scroll)
        if keep_scroll:
            self._pending_snapshot = None
        else:
            self._start_crossfade()
        self.selection_changed.emit()

    def _on_data_changed(self, top_left, bottom_right) -> None:
        for row in range(top_left.row(), bottom_right.row() + 1):
            if 0 <= row < len(self._rects):
                self.viewport().update(self._viewport_rect(row).adjusted(-2, -2, 2, 2))

    def _relayout(self, keep_scroll: bool, view_width: int | None = None) -> None:
        with profiling.span("view._relayout", min_ms=3.0):
            self._relayout_impl(keep_scroll, view_width)

    def _relayout_impl(self, keep_scroll: bool, view_width: int | None = None) -> None:
        if view_width is None:
            self._flip = None  # any ordinary relayout ends a width transition
            self._flip_timer.stop()
        bar = self.verticalScrollBar()
        ratio = bar.value() / self._content_height if keep_scroll and self._content_height else 0.0
        self._rows = []
        self._headers = []
        self._header_tops = []
        self._header_ranges = []
        self._rail_source = []
        self._rail = None
        self._rail_key = None
        self._wanted_span = None
        count = self._model.rowCount() if self._model else 0
        self._rects = [QRect() for _ in range(count)]
        self._row_of = [0] * count
        laid_out = view_width if view_width is not None else self.viewport().width()
        self._layout_view_width = laid_out
        width = max(1, laid_out - MARGIN_LEFT - MARGIN_RIGHT)
        y = TOP_PADDING
        if self._model is not None and count:
            aspects = [self._model.aspect_for(self._model.item_at(row)) for row in range(count)]
            for start, end, label in self._day_groups(count):
                if label:
                    self._headers.append((y, label, end - start))
                    self._header_tops.append(y)
                    self._header_ranges.append((start, end))
                    y += HEADER_HEIGHT
                if self._square:
                    packed = square_rows(end - start, width, self._row_height, TILE_GAP)
                else:
                    packed = justify_rows(aspects[start:end], width, self._row_height, TILE_GAP)
                for height, tiles in packed:
                    placed = []
                    for index, left, tile_width in tiles:
                        row = start + index
                        x = MARGIN_LEFT + left
                        self._rects[row] = QRect(x, y, tile_width, height)
                        self._row_of[row] = len(self._rows)
                        placed.append((row, x, tile_width))
                    self._rows.append(_Row(y, height, placed))
                    y += height + TILE_GAP
                y += GROUP_SPACING
        self._row_tops = [row.y for row in self._rows]
        self._content_height = y + BOTTOM_PADDING
        viewport_height = self.viewport().height()
        bar.setRange(0, max(0, self._content_height - viewport_height))
        bar.setPageStep(viewport_height)
        bar.setValue(round(ratio * self._content_height) if keep_scroll else 0)
        self._rebuild_rail_source()
        self._stop_all_gifs()  # tile sizes and positions changed: restart them for the new layout
        self._gif_timer.start()
        self.viewport().update()

    def _day_groups(self, count: int) -> list[tuple[int, int, str]]:
        model = self._model
        if model is None or not model.sort_mode().by_date:
            return [(0, count, "")]
        today = datetime.now().date()
        groups: list[tuple[int, int, str]] = []
        start = 0
        current = _day_of(model.item_at(0).sort_time)
        for row in range(1, count + 1):
            day = _day_of(model.item_at(row).sort_time) if row < count else None
            if row < count and day == current:
                continue
            groups.append((start, row, _format_group_day(current, today)))
            start, current = row, day
        return groups

    def _rebuild_rail_source(self) -> None:
        """Collect (y, timestamp) per layout row; the rail backend decides what the dots mean."""
        self._rail = None
        self._rail_key = None
        self._rail_source = []
        if self._model is None or not self._rows or not self._model.sort_mode().by_date:
            return
        self._rail_source = [
            (float(row.y), self._model.item_at(row.tiles[0][0]).sort_time) for row in self._rows if row.tiles
        ]

    def _rail_data(self) -> Rail | None:
        if not self._rail_source:
            return None
        top, bottom = self._timeline_track_bounds()
        span = max(1, bottom - top)
        max_scroll = self.verticalScrollBar().maximum()
        key = (len(self._rail_source), self._content_height, span, max_scroll)
        if self._rail is None or self._rail_key != key:
            self._rail = build_rail(self._rail_source, max_scroll, span)
            self._rail_key = key
        return self._rail

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._xfade_pixmap is not None:
            self._clear_crossfade()
        if self._flip is not None:
            # The layout for the final width is already computed; just let the tiles glide.
            if self._flip_progress() >= 1.0:
                self._finish_flip()
            else:
                self.viewport().update()
            return
        self._relayout(keep_scroll=True)

    def begin_width_flip(self, extra_width: int) -> None:
        """The view is about to grow (or shrink) by `extra_width` px over the next few frames.

        Lay the tiles out once for the final width and glide them there, instead of re-flowing
        the whole gallery on every frame of the sidebar animation.
        """
        self._finish_flip()
        if self._model is None or not self._rows or not self.isVisible() or extra_width == 0:
            return
        old_width = self.viewport().width()
        new_width = old_width + int(extra_width)
        if old_width <= 0 or new_width < 240:
            return
        offset = self.verticalScrollBar().value()
        bottom = offset + self.viewport().height()
        old_rects: dict[int, QRect] = {}
        first = max(0, bisect_right(self._row_tops, offset) - 1)
        for position in range(first, len(self._rows)):
            row = self._rows[position]
            if row.y > bottom:
                break
            for index, _x, _width in row.tiles:
                old_rects[index] = self._rects[index].translated(0, -offset)
        old_headers = {label: y - offset for y, label, _count in self._headers}
        self._relayout(keep_scroll=True, view_width=new_width)
        self._flip = _Flip(old_rects, old_headers, old_width, new_width)
        self._flip_timer.start(DURATION_SLOW + 400)
        self.viewport().update()

    def _flip_progress(self) -> float:
        flip = self._flip
        if flip is None:
            return 1.0
        span = flip.new_width - flip.old_width
        if span == 0:
            return 1.0
        # Follow the real geometry so the tiles stay locked to the sidebar's own easing.
        return max(0.0, min(1.0, (self.viewport().width() - flip.old_width) / span))

    def _finish_flip(self) -> None:
        if self._flip is None:
            return
        self._flip = None
        self._flip_timer.stop()
        if self.viewport().width() != self._layout_view_width:
            self._relayout(keep_scroll=True)
        else:
            self.viewport().update()

    def _animated_rect(self, index: int) -> QRect:
        rect = self._viewport_rect(index)
        flip = self._flip
        if flip is None:
            return rect
        old = flip.old_rects.get(index)
        if old is None:
            return rect
        progress = self._flip_progress()
        return QRect(
            round(old.x() + (rect.x() - old.x()) * progress),
            round(old.y() + (rect.y() - old.y()) * progress),
            round(old.width() + (rect.width() - old.width()) * progress),
            round(old.height() + (rect.height() - old.height()) * progress),
        )

    def _viewport_rect(self, row: int) -> QRect:
        return self._rects[row].translated(0, -self.verticalScrollBar().value())

    def _timeline_rect(self) -> QRect:
        w = self.viewport().width()
        h = self.viewport().height()
        height = max(0, h - TIMELINE_TOP - TIMELINE_BOTTOM)
        return QRect(max(0, w - TIMELINE_WIDTH - TIMELINE_MARGIN), TIMELINE_TOP, TIMELINE_WIDTH, height)

    def _timeline_track_bounds(self) -> tuple[int, int]:
        rect = self._timeline_rect()
        top = rect.top() + TIMELINE_END_PADDING
        bottom = max(top + 20, rect.bottom() - TIMELINE_END_PADDING)
        return top, bottom

    def _in_timeline(self, point: QPoint) -> bool:
        return self.verticalScrollBar().maximum() > 0 and self._timeline_rect().contains(point)

    def _scrub_to_y(self, y: int) -> None:
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        top, bottom = self._timeline_track_bounds()
        pos = max(0.0, min(1.0, (y - top) / max(1, bottom - top)))
        data = self._rail_data()
        frac = data.frac_of(pos) if data else pos
        self._stop_scroll()
        bar.setValue(round(frac * bar.maximum()))

    def _label_height(self) -> float:
        return self._year_static("0000").size().height()

    def _year_labels(self, data: Rail | None, top: int, span: int) -> list[tuple[float, RailStop]]:
        """The year labels that fit on the rail, as (y of the year boundary, stop). The label itself
        is drawn above the boundary."""
        labels: list[tuple[float, RailStop]] = []
        if data is None:
            return labels
        last = -999.0
        for stop in data.stops:
            if stop.kind != "major":
                continue
            yy = top + stop.pos * span
            if yy - last < LABEL_GAP - 0.5:
                continue
            last = yy
            labels.append((yy, stop))
        return labels

    def _jump_to_stop(self, stop: RailStop) -> bool:
        bar = self.verticalScrollBar()
        if not 0 <= stop.row < len(self._rows):
            return False
        self._stop_scroll()
        bar.setValue(max(0, min(bar.maximum(), self._rows[stop.row].y - TOP_PADDING - HEADER_HEIGHT)))
        return True

    def _label_stop_at(self, data: Rail | None, y: float, top: int, span: int) -> RailStop | None:
        """The year whose label (the strip just above its boundary) contains this y."""
        reach = self._label_height() + TIMELINE_LABEL_GAP
        for yy, stop in self._year_labels(data, top, span):
            if yy - reach <= y <= yy + 1.0:
                return stop
        return None

    def _snap_to_stop(self, y: int) -> bool:
        """Pressing on a year label, or on (or right next to) a dot, jumps to the first row of that bucket."""
        data = self._rail_data()
        bar = self.verticalScrollBar()
        if data is None or bar.maximum() <= 0:
            return False
        top, bottom = self._timeline_track_bounds()
        span = max(1, bottom - top)
        stop = self._label_stop_at(data, y, top, span)
        if stop is None:
            stop = data.nearest_pos((y - top) / span, TIMELINE_SNAP / span)
        if stop is None:
            return False
        return self._jump_to_stop(stop)

    def _date_at_scroll(self, scroll_value: int) -> str:
        if self._model is None or not self._rows or not self._model.sort_mode().by_date:
            return ""
        position = max(0, min(len(self._rows) - 1, bisect_right(self._row_tops, scroll_value + 40) - 1))
        row = self._rows[position]
        if not row.tiles:
            return ""
        item = self._model.item_at(row.tiles[0][0])
        return _format_month_year(item.sort_time)

    def _sticky_position(self) -> int | None:
        if not self._headers:
            return None
        position = bisect_right(self._header_tops, self.verticalScrollBar().value() + TOP_PADDING) - 1
        return max(0, position)

    def _sticky_header(self) -> tuple[str, int] | None:
        if not self._headers:
            return None
        position = bisect_right(self._header_tops, self.verticalScrollBar().value() + TOP_PADDING) - 1
        _y, text, count = self._headers[max(0, position)]
        return text, count

    def _rows_span(self, offset: int) -> tuple[int, int]:
        count = len(self._rows)
        first = max(0, bisect_right(self._row_tops, offset) - 1)
        last = min(count - 1, bisect_right(self._row_tops, offset + self.viewport().height()) - 1)
        return first, max(first, last)

    def _update_wanted(self, offset: int) -> None:
        if self._model is None or not self._rows:
            return
        count = len(self._rows)
        vis_first, vis_last = self._rows_span(offset)
        if self._scroll_running():
            dest_first, dest_last = self._rows_span(self._scroll_target)
        else:
            dest_first, dest_last = vis_first, vis_last
        span = (vis_first, vis_last, dest_first, dest_last)
        if span == getattr(self, "_wanted_span", None):
            return
        self._wanted_span = span
        first = max(0, min(vis_first, dest_first) - 2, dest_first - WANTED_REACH)
        last = min(count - 1, max(vis_last, dest_last) + 2, dest_last + WANTED_REACH)

        def edge(position: int, end: int) -> int:
            tiles = self._rows[position].tiles
            return tiles[end][0] if tiles else 0

        self._model.set_wanted_rows(edge(first, 0), edge(last, -1), edge(dest_first, 0), edge(dest_last, -1))

    def _paint_sticky_header(self, painter: QPainter) -> None:
        sticky = self._sticky_header()
        if sticky is None:
            return
        painter.save()
        self._paint_background(painter, QRect(0, 0, self.viewport().width(), TOP_PADDING + HEADER_HEIGHT - 2))
        position = self._sticky_position()
        self._draw_header(
            painter,
            MARGIN_LEFT + HEADER_INSET,
            TOP_PADDING,
            sticky[0],
            sticky[1],
            self._group_button(position),
        )
        painter.restore()

    def _draw_header(
        self, painter: QPainter, x: int, y: int, text: str, count: int, button: str | None = None
    ) -> None:
        painter.setFont(self._font_variant("header"))
        painter.setPen(_tone("text"))
        width = max(0, self.viewport().width() - x - MARGIN_RIGHT)
        painter.drawText(
            QRect(x, y, width, HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        advance = painter.fontMetrics().horizontalAdvance(text)
        painter.setFont(self._font_variant("count"))
        painter.setPen(_tone("text_muted"))
        painter.drawText(
            QRect(x + advance + 10, y, 96, HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            str(count),
        )
        if button is not None:
            rect = self._group_button_rect(x, y, text, count, button)
            accent = _tone("accent")
            border = QColor(accent)
            border.setAlpha(140)
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), rect.height() / 2, rect.height() / 2)
            painter.setFont(self._font_variant("pill"))
            painter.setPen(accent)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, button)

    def _group_button_rect(self, x: int, y: int, text: str, count: int, label: str) -> QRect:
        """The Select all / Deselect pill that sits right after a day header's count."""
        header = QFontMetrics(self._font_variant("header"))
        counter = QFontMetrics(self._font_variant("count"))
        pill = QFontMetrics(self._font_variant("pill"))
        left = x + header.horizontalAdvance(text) + 10 + counter.horizontalAdvance(str(count)) + GROUP_BUTTON_GAP
        width = pill.horizontalAdvance(label) + GROUP_BUTTON_PAD * 2
        return QRect(left, y + (HEADER_HEIGHT - GROUP_BUTTON_HEIGHT) // 2, width, GROUP_BUTTON_HEIGHT)

    def _group_button(self, position: int | None) -> str | None:
        """Label for a day header's button; None (no button) outside selection mode."""
        if not self.is_selecting() or position is None or not 0 <= position < len(self._header_ranges):
            return None
        start, end = self._header_ranges[position]
        return "Deselect" if self._selected.issuperset(range(start, end)) else "Select all"

    def _group_button_at(self, point: QPoint) -> int | None:
        """Header position whose Select all / Deselect button is under `point`."""
        if not self.is_selecting() or not self._headers or self._in_timeline(point):
            return None
        offset = self.verticalScrollBar().value()
        if point.y() < TOP_PADDING + HEADER_HEIGHT - 2:
            position, y_view = self._sticky_position(), TOP_PADDING
        else:
            position = bisect_right(self._header_tops, point.y() + offset) - 1
            if position < 0:
                return None
            y_view = self._headers[position][0] - offset
        label = self._group_button(position)
        if label is None:
            return None
        _y, text, count = self._headers[position]
        rect = self._group_button_rect(MARGIN_LEFT + HEADER_INSET, y_view, text, count, label)
        return position if rect.contains(point) else None

    def _toggle_group(self, position: int) -> None:
        if not 0 <= position < len(self._header_ranges):
            return
        start, end = self._header_ranges[position]
        rows = range(start, end)
        if self._selected.issuperset(rows):
            self._selected.difference_update(rows)
        else:
            self._selected.update(rows)
        self.viewport().update()
        self.selection_changed.emit()

    def _index_at(self, point: QPoint) -> int | None:
        if not self._rows or self._in_timeline(point):
            return None
        if point.y() < TOP_PADDING + HEADER_HEIGHT - 2:
            return None
        y = point.y() + self.verticalScrollBar().value()
        position = bisect_right(self._row_tops, y) - 1
        if position < 0:
            return None
        row = self._rows[position]
        if y >= row.y + row.height:
            return None
        for index, x, width in row.tiles:
            if x <= point.x() < x + width:
                return index
        return None

    def _zone_at(self, index: int, point: QPoint) -> str:
        rect = self._viewport_rect(index)
        if self._check_rect(rect).adjusted(-4, -4, 4, 4).contains(point):
            return "check"
        if self._heart_rect(rect).adjusted(-4, -4, 4, 4).contains(point):
            return "heart"
        return "tile"

    @staticmethod
    def _check_rect(rect: QRect) -> QRect:
        return QRect(rect.left() + CHECK_INSET, rect.top() + CHECK_INSET, CHECK_SIZE, CHECK_SIZE)

    @staticmethod
    def _heart_rect(rect: QRect) -> QRect:
        return QRect(
            rect.right() - CHECK_INSET - HEART_SIZE + 1,
            rect.top() + CHECK_INSET,
            HEART_SIZE,
            HEART_SIZE,
        )

    def ensure_visible(self, index: int) -> None:
        rect = self._rects[index]
        bar = self.verticalScrollBar()
        top, bottom = bar.value() + TOP_PADDING + HEADER_HEIGHT, bar.value() + self.viewport().height()
        if rect.top() - TILE_GAP < top:
            bar.setValue(max(0, rect.top() - TOP_PADDING - HEADER_HEIGHT))
        elif rect.bottom() + TILE_GAP > bottom:
            bar.setValue(rect.bottom() + BOTTOM_PADDING - self.viewport().height())

    # painting

    def _paint_background(self, painter: QPainter, rect: QRect) -> None:
        """The window's background picture behind `rect` (viewport coordinates), or the plain content color."""
        if not backdrop().paint(painter, self.viewport(), rect):
            painter.fillRect(rect, _tone("content"))

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        self._paint_background(painter, event.rect())
        offset = self.verticalScrollBar().value()
        if not self._rows:
            self._paint_empty(painter)
            self._paint_crossfade(painter)
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        started = profiling.tick()
        profiling.once("first gallery paint with photos")
        self._build_deadline = time.perf_counter() + TILE_BUILD_BUDGET_SECONDS
        self._starved: list[QRect] = []
        top = event.rect().top() + offset
        bottom = event.rect().bottom() + offset
        self._paint_headers(painter, top, bottom, offset)
        self._update_wanted(offset)
        drawn: set[int] = set()
        first = max(0, bisect_right(self._row_tops, top) - 1)
        for position in range(first, len(self._rows)):
            row = self._rows[position]
            if row.y > bottom:
                break
            for index, _x, _width in row.tiles:
                self._paint_tile(painter, index, offset)
                if self._flip is not None:
                    drawn.add(index)
        if self._flip is not None:
            for index in self._flip.old_rects:
                if index not in drawn and index < len(self._rects):
                    self._paint_tile(painter, index, offset)
        self._paint_sticky_header(painter)
        self._paint_timeline(painter)
        self._paint_crossfade(painter)
        pending = self._fading_rects + self._starved
        profiling.slow("paint", started, detail=f"rect={event.rect().width()}x{event.rect().height()} fading={len(self._fading_rects)} starved={len(self._starved)}")
        self._fading_rects = []
        self._starved = []
        if pending:
            if self._scroll_running():
                # Dropping these would leave grey holes once the glide lands on an unchanged value.
                self._repaint_after_scroll = True
            else:
                QTimer.singleShot(self._frame_interval_ms(), lambda rects=pending: self._update_rects(rects))

    def _update_rects(self, rects: list[QRect]) -> None:
        viewport = self.viewport()
        for rect in rects:
            viewport.update(rect)

    def _tile_pixmap(self, key: str, source: QPixmap, size: QSize) -> tuple[QPixmap | None, bool]:
        ratio = self.devicePixelRatioF()
        cached = self._tiles.get(key)
        if cached is not None:
            source_key, cached_size, cached_ratio, tile = cached
            if source_key == source.cacheKey() and cached_size == size and cached_ratio == ratio:
                self._tiles.move_to_end(key)
                return tile, True
        if time.perf_counter() > self._build_deadline:
            # Out of frame budget: reuse the previous size (scaled) rather than a grey hole.
            return (cached[3] if cached is not None else None), False
        pixel_width = max(1, int(ceil(size.width() * ratio)))
        pixel_height = max(1, int(ceil(size.height() * ratio)))
        tile = QPixmap(pixel_width, pixel_height)
        tile.setDevicePixelRatio(ratio)
        tile.fill(Qt.GlobalColor.transparent)
        target = QRectF(0.0, 0.0, pixel_width / ratio, pixel_height / ratio)
        painter = QPainter(tile)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(target, RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.drawPixmap(target, source, _cover_rect(QSizeF(source.size()), target.size()))
        painter.end()
        if cached is not None:
            self._tile_bytes -= cached[3].width() * cached[3].height() * 4
        self._tiles[key] = (source.cacheKey(), QSize(size), ratio, tile)
        self._tiles.move_to_end(key)
        self._tile_bytes += pixel_width * pixel_height * 4
        while self._tile_bytes > TILE_CACHE_BYTE_LIMIT and len(self._tiles) > 64:
            _evicted_key, evicted = self._tiles.popitem(last=False)
            self._tile_bytes -= evicted[3].width() * evicted[3].height() * 4
        return tile, True

    def _paint_empty(self, painter: QPainter) -> None:
        if not self._empty_text:
            return
        painter.setPen(_tone("text_muted"))
        painter.drawText(
            self.viewport().rect().adjusted(40, 0, -40, 0),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self._empty_text,
        )

    def _paint_headers(self, painter: QPainter, top: int, bottom: int, offset: int) -> None:
        flip = self._flip
        progress = self._flip_progress() if flip is not None else 1.0
        view_top, view_bottom = top - offset, bottom - offset
        for position, (y, text, count) in enumerate(self._headers):
            y_view = y - offset
            alpha = 1.0
            if flip is not None:
                before = flip.old_headers.get(text)
                if before is None:
                    alpha = progress
                else:
                    y_view = round(before + (y_view - before) * progress)
            if y_view + HEADER_HEIGHT < view_top or y_view > view_bottom:
                continue
            if alpha < 1.0:
                painter.setOpacity(alpha)
            self._draw_header(
                painter, MARGIN_LEFT + HEADER_INSET, y_view, text, count, self._group_button(position)
            )
            if alpha < 1.0:
                painter.setOpacity(1.0)

    def _paint_tile(self, painter: QPainter, index: int, offset: int) -> None:
        model = self._model
        item = model.item_at(index)
        animated = self._animated_rect(index)
        if self._flip is not None and not animated.intersects(self.viewport().rect()):
            return
        rect = QRectF(animated)
        selected = index in self._selected
        hovered = index == self._hover
        key = str(item.path)
        pixmap = self._gif_frames.get(key) or model.pixmap_for(item)
        tile = None
        exact = True
        if pixmap is not None and not pixmap.isNull():
            # While gliding, cache at the final size so the tiles are crisp once they land.
            target = self._rects[index].size() if self._flip is not None else QSize(int(rect.width()), int(rect.height()))
            tile, exact = self._tile_pixmap(key, pixmap, target)
        if tile is None:
            if pixmap is not None and not pixmap.isNull():
                self._draw_source(painter, rect, pixmap)
                self._starved.append(rect.toRect().adjusted(-1, -1, 1, 1))
            else:
                self._fill_rounded(painter, rect, _tone("placeholder"))
        else:
            if not exact:
                self._starved.append(rect.toRect().adjusted(-1, -1, 1, 1))
            fade = min(1.0, model.loaded_age(item) / FADE_SECONDS)
            if fade < 1.0:
                self._fading_rects.append(rect.toRect().adjusted(-1, -1, 1, 1))
                self._fill_rounded(painter, rect, _tone("placeholder"))
                painter.setOpacity(1.0 - (1.0 - fade) ** 3)
            if exact and self._flip is None:
                painter.drawPixmap(rect.topLeft(), tile)  # 1:1 blit, the fast path
            else:
                painter.drawPixmap(rect, tile, QRectF(0.0, 0.0, float(tile.width()), float(tile.height())))
            painter.setOpacity(1.0)
        if selected:
            self._fill_rounded(painter, rect, _tone("selected_tint"))
        if hovered:
            self._paint_hover_shade(painter, rect)

        badge = "NPI" if item.is_npi else item.format_label
        narrow_video = item.is_video and rect.width() < VIDEO_FORMAT_MIN_WIDTH
        format_shown = bool(badge) and bool(prefs().get("tile_attributes")) and not narrow_video
        if format_shown:
            self._paint_badge(painter, rect, badge)
        if key in self._duplicates and not item.is_video:
            self._paint_duplicate_badge(painter, rect, badge if format_shown else "")
        if item.is_video:
            self._paint_video_mark(painter, rect, item.duration)
        favorite = model.is_favorite(item)
        if hovered or favorite:
            heart = QRectF(self._heart_rect(rect.toRect()))
            self._fill_rounded(painter, heart, QColor(0, 0, 0, 120), heart.width() / 2)
            draw_icon(
                painter,
                "heart",
                heart.adjusted(5.5, 5.5, -5.5, -5.5),
                theme.palette().heart if favorite else "#ffffff",
                filled=favorite,
                stroke=2.0,
            )
        if hovered or selected or self._select_mode:
            self._paint_check(painter, rect, selected)
        if selected or index == self._context_target:
            self._paint_border(painter, rect, _tone("accent"), BORDER_WIDTH)
        elif self._keyboard and index == self._current and self.hasFocus():
            self._paint_border(painter, rect, _tone("accent"), 2.0)

    @staticmethod
    def _draw_source(painter: QPainter, rect: QRectF, source: QPixmap) -> None:
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.drawPixmap(rect, source, _cover_rect(QSizeF(source.size()), rect.size()))
        painter.restore()

    @staticmethod
    def _fill_rounded(painter: QPainter, rect: QRectF, color: QColor, radius: float = RADIUS) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, radius, radius)

    def _paint_timeline(self, painter: QPainter) -> None:
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        rail = QRectF(self._timeline_rect())
        top, bottom = self._timeline_track_bounds()
        span = max(1, bottom - top)
        active = self._timeline_hovered or self._timeline_dragging

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if active:
            # Windows Photos style: no resting background, just a faint wash while hovering/dragging.
            wash = QColor(_tone("timeline_bg"))
            wash.setAlpha(120)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(wash)
            painter.drawRoundedRect(rail, rail.width() / 2, rail.width() / 2)

        current_frac = bar.value() / max(1, bar.maximum())
        current_year = ""
        if self._model and self._rows and self._model.sort_mode().by_date:
            position = max(0, min(len(self._rows) - 1, bisect_right(self._row_tops, bar.value() + 20) - 1))
            current_year = _format_year(self._model.item_at(self._rows[position].tiles[0][0]).sort_time)

        centre_x = rail.center().x()
        data = self._rail_data()
        stops = data.stops if data else ()

        # Each year label sits just above the year boundary; the position marker is drawn at the boundary,
        # so it reads "2026" with the marker right under it.
        labels = self._year_labels(data, top, span)
        label_h = self._label_height()
        drawn_stops: set[RailStop] = set()
        painter.setFont(self._font_variant("year"))
        for yy, stop in labels:
            label = self._year_static(stop.caption)
            size = label.size()
            text_y = max(rail.top(), yy - size.height() - TIMELINE_LABEL_GAP)
            painter.setPen(_tone("timeline_active") if stop.caption == current_year else _tone("timeline_year"))
            painter.drawStaticText(QPointF(centre_x - size.width() / 2, text_y), label)
            drawn_stops.add(stop)

        # One dot per bucket: every dot means the same unit (day/week/month/quarter/year, chosen by
        # the rail backend). A year start whose label is hidden for lack of room still gets its dot.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_tone("timeline_tick"))
        last_dot = -999.0
        for stop in stops:
            if stop in drawn_stops:
                continue
            dy = top + stop.pos * span
            if any(-(label_h + 4) <= dy - ly <= 3 for ly, _stop in labels):
                continue  # under a year label
            if dy - last_dot < TIMELINE_DOT_MERGE:
                continue  # touching dots are drawn as one; every stop still exists for hover and jumping
            last_dot = dy
            painter.drawEllipse(QPointF(centre_x + TIMELINE_DOT_OFFSET, dy), TIMELINE_DOT_RADIUS, TIMELINE_DOT_RADIUS)

        # Current position: a wide horizontal bar across the rail, as in Windows Photos.
        marker_pos = data.pos_of(current_frac) if data else current_frac
        marker_y = top + marker_pos * span
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_tone("timeline_thumb"))
        painter.drawRoundedRect(
            QRectF(centre_x - TIMELINE_MARKER_WIDTH / 2, marker_y - 1.5, TIMELINE_MARKER_WIDTH, 3.0), 1.5, 1.5
        )

        if self._timeline_dragging:  # the date label only shows while scrubbing, not on a plain hover
            hover_pos = max(0.0, min(1.0, (self._timeline_pointer_y - top) / span))
            hover_frac = data.frac_of(hover_pos) if data else hover_pos
            stop = self._label_stop_at(data, self._timeline_pointer_y, top, span)
            if stop is None and data:
                stop = data.stop_at(min(1.0, hover_frac + 40 / max(1, bar.maximum())))
            label = stop.label if stop else self._date_at_scroll(round(hover_frac * bar.maximum()))
            if label:
                painter.setFont(self._font_variant("pill"))
                pill_w = painter.fontMetrics().horizontalAdvance(label) + 30
                pill_h = 32
                pill_y = max(rail.top(), min(rail.bottom() - pill_h, self._timeline_pointer_y - pill_h / 2))
                pill_rect = QRectF(rail.left() - pill_w - 10, pill_y, pill_w, pill_h)
                painter.setBrush(theme.float_color("pill_bg"))
                painter.setPen(QPen(_tone("pill_border"), 1.0))
                painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)
                painter.setPen(_tone("pill_text"))
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.restore()

    @staticmethod
    def _paint_hover_shade(painter: QPainter, rect: QRectF) -> None:
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, RADIUS, RADIUS)
        painter.setClipPath(clip)
        solid, clear = QColor(0, 0, 0, SHADE_ALPHA), QColor(0, 0, 0, 0)
        top = QRectF(rect.left(), rect.top(), rect.width(), SHADE_HEIGHT)
        bottom = QRectF(rect.left(), rect.bottom() - SHADE_HEIGHT, rect.width(), SHADE_HEIGHT)
        for area, start, end in ((top, solid, clear), (bottom, clear, solid)):
            gradient = QLinearGradient(area.topLeft(), area.bottomLeft())
            gradient.setColorAt(0, start)
            gradient.setColorAt(1, end)
            painter.fillRect(area, gradient)
        painter.restore()

    @staticmethod
    def _paint_border(painter: QPainter, rect: QRectF, color: QColor, width: float) -> None:
        pen = QPen(color)
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = width / 2
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), RADIUS, RADIUS)

    def _paint_check(self, painter: QPainter, rect: QRectF, selected: bool) -> None:
        circle = QRectF(self._check_rect(rect.toRect()))
        painter.setBrush(_tone("accent") if selected else QColor(0, 0, 0, 75))
        pen = QPen(_tone("accent") if selected else QColor("#ffffff"))
        pen.setWidthF(1.6)
        painter.setPen(pen)
        painter.drawEllipse(circle.adjusted(1, 1, -1, -1))
        if selected:
            draw_icon(painter, "check", circle.adjusted(5, 5, -5, -5), _tone("on_accent"), stroke=3.0)

    def _badge_pixmap(self, label: str) -> QPixmap:
        ratio = self.devicePixelRatioF()
        cached = self._badges.get(label)
        if cached is not None and cached[0] == ratio:
            return cached[1]
        font = self._font_variant("badge")
        width = max(BADGE_WIDTH, QFontMetrics(font).horizontalAdvance(label) + BADGE_PADDING * 2)
        pixmap = QPixmap(int(ceil(width * ratio)), int(ceil(BADGE_HEIGHT * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(0.0, 0.0, width, BADGE_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BADGE_BACKGROUND)
        painter.drawRoundedRect(area, BADGE_HEIGHT / 2, BADGE_HEIGHT / 2)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(area, Qt.AlignmentFlag.AlignCenter, label)
        painter.end()
        self._badges[label] = (ratio, pixmap)
        return pixmap

    def _paint_badge(self, painter: QPainter, rect: QRectF, label: str) -> None:
        painter.drawPixmap(QPointF(rect.left() + 8, rect.bottom() - BADGE_HEIGHT - 8), self._badge_pixmap(label))

    def set_duplicate_paths(self, paths: set[str]) -> None:
        """Photos to mark as copies of another photo (a small badge next to the format badge)."""
        if paths == self._duplicates:
            return
        self._duplicates = set(paths)
        self.viewport().update()

    def _duplicate_badge_pixmap(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        cached = self._duplicate_badge
        if cached is not None and cached[0] == ratio:
            return cached[1]
        side = BADGE_HEIGHT
        pixmap = QPixmap(int(ceil(side * ratio)), int(ceil(side * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BADGE_BACKGROUND)
        painter.drawEllipse(QRectF(0.0, 0.0, side, side))
        draw_icon(painter, "copy", QRectF(4.0, 4.0, side - 8.0, side - 8.0), "#ffffff", stroke=2.2)
        painter.end()
        self._duplicate_badge = (ratio, pixmap)
        return pixmap

    def _paint_duplicate_badge(self, painter: QPainter, rect: QRectF, format_label: str) -> None:
        """Right after the format badge (or at the corner when that one is not shown)."""
        x = rect.left() + 8
        if format_label:
            x += self._badge_pixmap(format_label).deviceIndependentSize().width() + 4
        if x + BADGE_HEIGHT + 4 > rect.right():
            return  # a tile this narrow has no room for it
        painter.drawPixmap(QPointF(x, rect.bottom() - BADGE_HEIGHT - 8), self._duplicate_badge_pixmap())

    def _paint_video_mark(self, painter: QPainter, rect: QRectF, duration: float) -> None:
        """What tells a video tile from a photo: a play button in the middle and the clip length."""
        if min(rect.width(), rect.height()) >= VIDEO_PLAY_MIN_SIDE:
            diameter = max(32.0, min(52.0, min(rect.width(), rect.height()) * 0.3))
            circle = QRectF(0.0, 0.0, diameter, diameter)
            circle.moveCenter(rect.center())
            self._fill_rounded(painter, circle, QColor(0, 0, 0, 140), diameter / 2)
            inset = diameter * 0.3
            glyph = circle.adjusted(inset + diameter * 0.04, inset, -inset + diameter * 0.04, -inset)
            draw_icon(painter, "play", glyph, "#ffffff", filled=True, stroke=1.6)
        if duration > 0 and rect.width() >= VIDEO_DURATION_MIN_WIDTH:
            pixmap = self._badge_pixmap(format_duration(duration))
            width = pixmap.deviceIndependentSize().width()
            painter.drawPixmap(QPointF(rect.right() - width - 8, rect.bottom() - BADGE_HEIGHT - 8), pixmap)

    # interaction

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        if self._timeline_dragging:
            self._timeline_pointer_y = point.y()
            self._scrub_to_y(point.y())
            self.viewport().update()
            event.accept()
            return

        in_timeline = self._in_timeline(point)
        if in_timeline != self._timeline_hovered or in_timeline:
            self._timeline_hovered = in_timeline
            self._timeline_pointer_y = point.y()
            self.viewport().update()

        if in_timeline:
            if self._hover is not None:
                self.viewport().update(self._viewport_rect(self._hover).adjusted(-2, -2, 2, 2))
                self._hover = None
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            super().mouseMoveEvent(event)
            return

        index = self._index_at(point)
        if index != self._hover:
            for row in (self._hover, index):
                if row is not None:
                    self.viewport().update(self._viewport_rect(row).adjusted(-2, -2, 2, 2))
            self._hover = index
            if prefs().get("gif_autoplay") == "hover":
                self._sync_gifs()
        over_button = index is None and self._group_button_at(point) is not None
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if index is not None or over_button else Qt.CursorShape.ArrowCursor
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self.viewport().update(self._viewport_rect(self._hover).adjusted(-2, -2, 2, 2))
            self._hover = None
            self._sync_gifs()
        if self._timeline_hovered:
            self._timeline_hovered = False
            self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._keyboard = False
        self.setFocus()
        point = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and self._in_timeline(point):
            self._timeline_dragging = True
            self._timeline_pointer_y = point.y()
            if not self._snap_to_stop(point.y()):
                self._scrub_to_y(point.y())
            self.viewport().update()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            group = self._group_button_at(point)
            if group is not None:
                self._group_pressed = group
                event.accept()
                return
            self._pressed = self._index_at(point)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._timeline_dragging:
            self._timeline_dragging = False
            self.viewport().update()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._group_pressed is not None:
            group, self._group_pressed = self._group_pressed, None
            if self._group_button_at(event.position().toPoint()) == group:
                self._toggle_group(group)
            event.accept()
            return
        pressed, self._pressed = self._pressed, None
        point = event.position().toPoint()
        index = self._index_at(point)
        if event.button() == Qt.MouseButton.LeftButton and index is None and pressed is None:
            if self.is_selecting():
                self.clear_selection()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or index is None or index != pressed:
            super().mouseReleaseEvent(event)
            return
        self._current = index
        zone = self._zone_at(index, point)
        modifiers = event.modifiers()
        if zone == "heart" and self._model is not None:
            self._model.toggle_favorite(index)
        elif zone == "check" or modifiers & Qt.KeyboardModifier.ControlModifier:
            self._toggle(index)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and self._anchor is not None:
            self._select_range(self._anchor, index)
        elif self.is_selecting():
            self._toggle(index)
        else:
            self._anchor = index
            self.activated.emit(index)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            if self._in_timeline(point):
                event.accept()
                return
            index = self._index_at(point)
            if index is not None and self._zone_at(index, point) == "tile":
                if self.is_selecting():
                    self._current = index
                    self.activated.emit(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._model is None:
            event.ignore()
            return
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            index = self.current_row()
            if index is None:
                event.ignore()
                return
            global_pos = self.viewport().mapToGlobal(self._viewport_rect(index).center())
        else:
            index = self._index_at(event.pos())
            global_pos = event.globalPos()
        if index is None:
            event.ignore()
            return
        self._current = index
        if self.is_selecting() and index not in self._selected:
            self._select_single(index)
        self.context_menu_requested.emit(index, global_pos)
        event.accept()

    def _select_single(self, index: int) -> None:
        self._selected = {index}
        self._anchor = index
        self.viewport().update()
        self.selection_changed.emit()

    def _toggle(self, index: int) -> None:
        if index in self._selected:
            self._selected.discard(index)
        else:
            self._selected.add(index)
        self._anchor = index
        self.viewport().update()
        self.selection_changed.emit()

    def _select_range(self, first: int, last: int) -> None:
        low, high = sorted((first, last))
        self._selected.update(range(low, high + 1))
        self.viewport().update()
        self.selection_changed.emit()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            units = self._wheel_input.zoom_units(event)
            step = round(WHEEL_ZOOM_STEP * units)
            if step:
                self.set_row_height(self._row_height + step)
                self.row_height_changed.emit(self._row_height)
            event.accept()
            return
        if event.modifiers() or event.angleDelta().y() == 0:
            super().wheelEvent(event)
            return
        bar = self.verticalScrollBar()
        if self._wheel_input.is_precision(event):
            self._start_smooth(self._wheel_input.pixels(event))
            self._update_wanted(bar.value())
            event.accept()
            return
        base = self._scroll_target if self._scroll_running() else bar.value()
        target = max(
            bar.minimum(), min(bar.maximum(), base - round(event.angleDelta().y() * WHEEL_PIXELS_PER_NOTCH / 120))
        )
        self._start_scroll(target)
        self._update_wanted(bar.value())
        event.accept()

    def _hide_tip(self) -> None:
        self._tip_timer.stop()
        self._tip_pending = None
        self._tip_index = None
        self._tip.hide()

    def _track_tip(self, point: QPoint, global_point: QPoint) -> None:
        index = None if self._in_timeline(point) else self._index_at(point)
        if index is None:
            self._hide_tip()
            return
        if index == self._tip_index:
            return
        if index == self._tip_pending:
            moved = (global_point - self._tip_anchor).manhattanLength()
            if moved < TIP_TOLERANCE_PX:
                return
        self._tip.hide()
        self._tip_index = None
        self._tip_pending = index
        self._tip_anchor = global_point
        self._tip_timer.start()

    def _show_tip(self) -> None:
        index = self._tip_pending
        self._tip_pending = None
        if index is None or self._model is None:
            return
        cursor = QCursor.pos()
        point = self.viewport().mapFromGlobal(cursor)
        if not self.viewport().rect().contains(point) or self._index_at(point) != index:
            return
        self._tip_index = index
        self._tip.show_text(self._model.item_at(index).name, cursor)

    def viewportEvent(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            return True
        if kind == QEvent.Type.MouseMove and self._model is not None:
            self._track_tip(event.pos(), event.globalPos())
        elif kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self._hide_tip()
        return super().viewportEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._gif_timer.start()

    def hideEvent(self, event) -> None:
        self._stop_all_gifs()
        self._hide_tip()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        count = len(self._rects)
        if key == Qt.Key.Key_Escape and self.is_selecting():
            self.clear_selection()
        elif key == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
        elif count and key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        ):
            self._move_current(key)
        elif count and self._current is not None and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(self._current)
        elif count and self._current is not None and key == Qt.Key.Key_Space:
            self._toggle(self._current)
        else:
            super().keyPressEvent(event)

    def _move_current(self, key) -> None:
        count = len(self._rects)
        current = self._current if self._current is not None else (self._hover or 0)
        if key == Qt.Key.Key_Left:
            target = current - 1
        elif key == Qt.Key.Key_Right:
            target = current + 1
        elif key == Qt.Key.Key_Home:
            target = 0
        elif key == Qt.Key.Key_End:
            target = count - 1
        else:
            target = self._vertical_neighbour(current, -1 if key == Qt.Key.Key_Up else 1)
        if self._current is None:
            target = current
        self._current = max(0, min(count - 1, target))
        self._keyboard = True
        self.ensure_visible(self._current)
        self.viewport().update()

    def _vertical_neighbour(self, index: int, direction: int) -> int:
        position = self._row_of[index] + direction
        if not 0 <= position < len(self._rows):
            return index
        centre = self._rects[index].center().x()
        return min(self._rows[position].tiles, key=lambda tile: abs(tile[1] + tile[2] / 2 - centre))[0]


def _cover_rect(source: QSizeF, target: QSizeF) -> QRectF:
    scale = max(target.width() / source.width(), target.height() / source.height())
    width = target.width() / scale
    height = target.height() / scale
    return QRectF((source.width() - width) / 2, (source.height() - height) / 2, width, height)