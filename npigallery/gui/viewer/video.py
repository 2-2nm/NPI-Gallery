"""Video in the viewer: a surface that plays the clip and a floating control bar.

QtMultimedia is imported lazily (`video_playback_available()`), so the gallery still starts on a
machine without it; the gallery then falls back to the system player.

Frames are painted by `VideoSurface` itself (QMediaPlayer -> QVideoSink) instead of a QVideoWidget:
a native video widget would sit on top of the floating glass controls and break their fade.

Mouse on the picture: a click in the left / right 20 % goes to the previous / next item, a double-click
there skips 5 or 10 s back / forward (Settings > Videos), a click in the middle plays or pauses. The bar
below has a seek slider: click or drag to jump.
"""

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ..common.icons import draw_icon
from ..theme.preferences import prefs
from . import geometry as geo
from .overlays import GlassPanel, IconButton
from .style import CANVAS, GLASS_BORDER, TEXT, TEXT_DIM, accent_pair

DEBUG = os.environ.get("NPI_VIDEO_DEBUG") == "1"  # prints the renderer decisions to stderr


def _dbg(message: str) -> None:
    if DEBUG:
        import sys

        print(f"[npigallery video] {message}", file=sys.stderr, flush=True)


SOFTWARE_AFTER_MS = 2500  # an AV1 clip with no picture after this long is handed to the software player
BAR_HEIGHT = 40
BAR_MAX_WIDTH = 640
_QT_MISSING = False


def video_playback_available() -> bool:
    global _QT_MISSING
    if _QT_MISSING:
        return False
    try:
        from PySide6 import QtMultimedia  # noqa: F401
    except ImportError:
        _QT_MISSING = True
        return False
    return True


class _PlayBadge(QWidget):
    """The round play button shown over a paused (or finished) clip."""

    SIZE = 64

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(GLASS_BORDER, 1))
        painter.setBrush(QColor(20, 20, 20, 150))
        painter.drawEllipse(rect)
        draw_icon(painter, "play", rect.adjusted(19, 19, -19, -19), TEXT, filled=True)


class VideoSurface(QWidget):
    """Plays one clip at a time on the viewer's dark stage.

    Two ways to show the frames (NPI_VIDEO_RENDERER=gpu|cpu, default gpu):
    - gpu: a QVideoWidget renders the decoded frames on the GPU (no copy back to the CPU).
    - cpu: the frames are converted to images and painted here; slower, kept as a fallback.
    """

    pressed = Signal()  # any mouse press (the window uses it to end name editing)
    side_clicked = Signal(int)  # -1 / +1: a single click on the left / right edge (go to the previous / next item)
    caption_pressed = Signal()
    caption_double_clicked = Signal()
    position_changed = Signal(int)  # ms
    duration_changed = Signal(int)  # ms
    playing_changed = Signal(bool)
    ended = Signal()
    failed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

        self._QMediaPlayer = QMediaPlayer
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.caption_hit: Callable[[QPoint], bool] | None = None
        self._insets = (0, 0, 0, 0)
        self._frame = None
        self._soft = None  # SoftwarePlayer (PyAV, CPU) while QtMultimedia cannot play the clip, else None
        self._tried_soft = False  # the software player is tried once per clip
        self._got_frame = False  # QtMultimedia has delivered a picture for the current clip
        self._loop = False
        self._frame_serial = 0  # bumped for every new frame; keys the scaled copy below
        self._fitted: QImage | None = None  # the current frame scaled to its on-screen size
        self._fitted_key: tuple | None = None
        self._press_pos: QPoint | None = None
        self._side_zone = 0  # edge of a click that is waiting to see whether it becomes a double-click
        self._side_timer = QTimer(self)
        self._side_timer.setSingleShot(True)
        self._side_timer.timeout.connect(self._emit_side_click)
        self._playing = False
        self._path: Path | None = None

        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._widget = None
        if os.environ.get("NPI_VIDEO_RENDERER", "gpu").strip().lower() != "cpu":
            try:
                from PySide6.QtMultimediaWidgets import QVideoWidget

                self._widget = QVideoWidget(self)
                self._widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
                self._widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                # Between two clips the widget shows its own background: keep it the stage's dark
                # color (it follows the app palette otherwise, which is bright in the light theme).
                palette = self._widget.palette()
                palette.setColor(QPalette.ColorRole.Window, CANVAS)
                palette.setColor(QPalette.ColorRole.Base, CANVAS)
                self._widget.setPalette(palette)
                self._widget.setAutoFillBackground(True)
                self._player.setVideoOutput(self._widget)
            except ImportError:
                self._widget = None
        self._badge = _PlayBadge(self)
        self._gpu_active = self._widget is not None
        self._gpu_wanted = self._gpu_active  # what the window asked for; kept while the software player is on
        self._watch = QTimer(self)  # an AV1 clip that shows no picture after a while is handed to PyAV
        self._watch.setSingleShot(True)
        self._watch.timeout.connect(self._check_stalled)
        if self._widget is not None:
            try:
                self._widget.videoSink().videoFrameChanged.connect(self._note_frame)
            except AttributeError:
                pass
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)
        if self._widget is None:
            self._player.setVideoSink(self._sink)
        self._player.positionChanged.connect(lambda ms: self.position_changed.emit(int(ms)))  # qlonglong -> int
        self._player.durationChanged.connect(lambda ms: self.duration_changed.emit(int(ms)))
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)

    # // control

    def load(self, path: Path, autoplay: bool = True) -> None:
        self._cancel_side_click()
        self._leave_software()
        self._path = path
        self._frame = None
        self._got_frame = False
        self._tried_soft = False
        self.update()
        if os.environ.get("NPI_AV1_SOFTWARE") == "1":  # always decode AV1 on the CPU (handy to test it)
            from . import swplayer

            if swplayer.available() and swplayer.video_codec_name(path) == "av1" and self._start_software(autoplay):
                return
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if autoplay:
            self._player.play()
            self._watch.start(SOFTWARE_AFTER_MS)

    def release(self) -> None:
        self._cancel_side_click()
        """Let go of the file (Windows will not delete or rename a clip that is still open)."""
        self._watch.stop()
        self._leave_software()
        self._player.stop()
        self._player.setSource(QUrl())
        self._frame = None
        self._path = None
        self.update()
        self._update_badge()

    def is_playing(self) -> bool:
        return self._playing

    def toggle_play(self) -> None:
        if self._soft is not None:
            if self._soft.is_playing():
                self._soft.pause()
            else:
                self._soft.play()
            return
        if self._player.source().isEmpty():
            return
        if self._playing:
            self._player.pause()
        else:
            self._player.play()  # from the start again when the clip had ended

    def set_loop(self, on: bool) -> None:
        """Play the clip again from the start whenever it reaches the end (no `ended` signal then)."""
        self._loop = bool(on)
        if self._soft is not None:
            self._soft.set_loop(on)
        loops = self._QMediaPlayer.Loops
        self._player.setLoops(loops.Infinite if on else loops.Once)

    def seek_to(self, ms: int) -> None:
        if self._soft is not None:
            self._soft.seek(geo.clamp_position(ms, self._soft.duration()))
            return
        self._player.setPosition(geo.clamp_position(ms, self._player.duration()))

    def seek_by(self, delta_ms: int) -> None:
        self.seek_to(self.position() + delta_ms)

    def seek_step_ms(self) -> int:
        """5 or 10 seconds, from Settings > Videos."""
        try:
            seconds = int(prefs().get("video_seek_seconds"))
        except (TypeError, ValueError):
            seconds = geo.SEEK_STEP_MS // 1000
        return (seconds if seconds in (5, 10) else geo.SEEK_STEP_MS // 1000) * 1000

    def _cancel_side_click(self) -> None:
        self._side_timer.stop()
        self._side_zone = 0

    def _emit_side_click(self) -> None:
        zone, self._side_zone = self._side_zone, 0
        if zone:
            self.side_clicked.emit(zone)

    def duration(self) -> int:
        return self._soft.duration() if self._soft is not None else self._player.duration()

    def position(self) -> int:
        return self._soft.position() if self._soft is not None else self._player.position()

    def set_muted(self, muted: bool) -> None:
        if self._soft is not None:
            self._soft.set_muted(muted)
        self._audio.setMuted(muted)

    def is_muted(self) -> bool:
        return self._audio.isMuted()

    def set_volume(self, level: float) -> None:
        """`level` is the slider position (0-1); loudness is perceived roughly quadratically, so square it."""
        level = min(1.0, max(0.0, level))
        self._audio.setVolume(level * level)
        if self._soft is not None:
            self._soft.set_volume(level * level)

    def set_insets(self, left: int, top: int, right: int, bottom: int) -> None:
        self._insets = (left, top, right, bottom)
        self._place()
        self.update()

    def set_gpu(self, enabled: bool) -> None:
        self._gpu_wanted = enabled
        if self._soft is not None:
            return  # the software player paints on the CPU path whatever the window asks for
        if self._widget is None or enabled == self._gpu_active:
            return
        self._gpu_active = enabled
        if enabled:
            self._widget.show()
            self._player.setVideoOutput(self._widget)
        else:
            self._widget.hide()
            self._player.setVideoSink(self._sink)
        self._frame = None
        self._place()
        self._update_badge()
        if not self._playing:
            self._player.setPosition(self._player.position())
        self.update()

    def _place(self) -> None:
        stage = self._stage().toRect()
        if self._widget is not None:
            self._widget.setGeometry(stage)
        self._badge.move(stage.center().x() - _PlayBadge.SIZE // 2, stage.center().y() - _PlayBadge.SIZE // 2)
        self._badge.raise_()
        self._push_target()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _update_badge(self) -> None:
        # Not while a clip is still loading: switching clips stops the player for a moment, and the
        # bright play button would flash up between the two.
        if self._soft is not None:
            self._badge.setVisible(False)  # the software player's pause badge is painted by paintEvent
            return
        status = self._QMediaPlayer.MediaStatus
        loaded = self._player.mediaStatus() in (
            status.LoadedMedia,
            status.BufferingMedia,
            status.BufferedMedia,
            status.StalledMedia,
            status.EndOfMedia,
        )
        self._badge.setVisible(loaded and not self._playing and not self._player.source().isEmpty() and self._gpu_active)

    # // player signals

    def _on_frame(self, frame) -> None:
        if isinstance(frame, QImage) or frame.isValid():  # QtMultimedia also sends empty frames (new clip, stop)
            self._got_frame = True
        self._frame = frame
        self._frame_serial += 1
        self.update()

    def _note_frame(self, frame) -> None:
        if frame.isValid():
            self._got_frame = True  # the video widget (GPU path) has a picture

    def _on_state(self, state) -> None:
        if self._soft is not None:
            return  # QtMultimedia is stopped; the software player reports its own state
        playing = state == self._QMediaPlayer.PlaybackState.PlayingState
        if playing != self._playing:
            self._playing = playing
            self.playing_changed.emit(playing)
        self._update_badge()

    def _on_status(self, status) -> None:
        if self._soft is not None:
            return
        self._update_badge()
        if status == self._QMediaPlayer.MediaStatus.EndOfMedia:
            self.ended.emit()

    def _on_error(self, _error, message: str) -> None:
        if self._soft is not None:
            return
        _dbg(f"QtMultimedia error: {message!r}")
        if self._start_software(True):  # QtMultimedia cannot decode it (typically AV1 without a GPU for it)
            return
        self.failed.emit(message or "Unsupported video")

    # // software (CPU) playback through PyAV

    def _check_stalled(self) -> None:
        """QtMultimedia sometimes reports no error for an AV1 clip it cannot decode: it just shows nothing."""
        if self._soft is not None or self._got_frame or self._path is None:
            return
        from . import swplayer

        codec = swplayer.video_codec_name(self._path) if swplayer.available() else ""
        _dbg(f"no picture after {SOFTWARE_AFTER_MS} ms: codec={codec!r}, state={self._player.playbackState()}")
        if codec:  # any codec: QtMultimedia has none of them without the right decoder (AV1, HEVC, ...)
            self._start_software(True)

    def _start_software(self, autoplay: bool) -> bool:
        """Play the current clip with PyAV on the CPU. False when that is not possible (no PyAV, or tried already)."""
        if self._soft is not None or self._tried_soft or self._path is None:
            return False
        try:
            from . import swplayer
        except ImportError:
            return False
        if not swplayer.available():
            return False
        self._tried_soft = True
        _dbg("switching to the software (PyAV) player")
        self._watch.stop()
        soft = swplayer.SoftwarePlayer(self)
        soft.frame_ready.connect(self._on_frame)
        soft.position_changed.connect(self.position_changed)
        soft.duration_changed.connect(self.duration_changed)
        soft.playing_changed.connect(self._soft_playing)
        soft.ended.connect(self.ended)
        soft.failed.connect(self._soft_failed)
        self._soft = soft  # from here on QtMultimedia's own signals are ignored
        self._player.stop()
        self._player.setSource(QUrl())
        if self._widget is not None:
            self._widget.hide()
        self._gpu_active = False
        self._badge.hide()
        soft.set_loop(self._loop)
        soft.set_volume(self._audio.volume())
        soft.set_muted(self._audio.isMuted())
        self._frame = None
        self._push_target()
        soft.open(self._path, autoplay)
        self.update()
        return True

    def _leave_software(self) -> None:
        soft, self._soft = self._soft, None
        if soft is None:
            return
        soft.close()
        soft.deleteLater()
        if self._playing:
            self._playing = False
            self.playing_changed.emit(False)
        if self._widget is not None:  # give the clip back to the GPU path the window asked for
            self._gpu_active = not self._gpu_wanted
            self.set_gpu(self._gpu_wanted)

    def _push_target(self) -> None:
        if self._soft is not None:
            stage = self._stage()
            ratio = self.devicePixelRatioF()
            self._soft.set_target(round(stage.width() * ratio), round(stage.height() * ratio))

    def _soft_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.playing_changed.emit(playing)
        self.update()

    def _soft_failed(self, message: str) -> None:
        self.failed.emit(message or "Unsupported video")

    # // painting

    def _stage(self) -> QRectF:
        left, top, right, bottom = self._insets
        return QRectF(left, top, max(1, self.width() - left - right), max(1, self.height() - top - bottom))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), CANVAS)
        if self._gpu_active:
            return  # the video widget draws the frames
        stage = self._stage()
        image = self._fitted_frame(stage)
        if image is not None:
            ratio = image.devicePixelRatio() or 1.0
            # The frame is already exactly as many device pixels as it will cover, so it is blitted 1:1 on a
            # device-pixel boundary: no resampling by Qt (that is what made the edges jagged), no smearing.
            x = round(stage.center().x() * ratio - image.width() / 2) / ratio
            y = round(stage.center().y() * ratio - image.height() / 2) / ratio
            painter.drawImage(QPointF(x, y), image)
            if not self._playing:
                self._paint_play_badge(painter, stage)

    def _fitted_frame(self, stage: QRectF) -> QImage | None:
        """The current frame scaled to exactly the size it has on screen (device pixels), with Qt's
        area-weighted SmoothTransformation (every source pixel counts when shrinking, bilinear when
        enlarging). Painting a frame at a different size would use Qt's plain sampling, which is jagged
        while playing. The result is kept, so repaints of the same frame (pause, overlays) cost nothing."""
        if self._frame is None:
            return None
        if isinstance(self._frame, QImage):  # from the software player, already scaled by swscale
            if self._frame.isNull():
                return None
        elif not self._frame.isValid():
            return None
        ratio = self.devicePixelRatioF()
        key = (self._frame_serial, round(stage.width() * ratio), round(stage.height() * ratio), ratio)
        if self._fitted is not None and self._fitted_key == key:
            return self._fitted
        source = self._frame if isinstance(self._frame, QImage) else self._frame.toImage()
        if source.isNull():
            return None
        scale = min(stage.width() * ratio / source.width(), stage.height() * ratio / source.height())
        width, height = max(1, round(source.width() * scale)), max(1, round(source.height() * scale))
        if abs(width - source.width()) > 1 or abs(height - source.height()) > 1:
            source = source.scaled(
                width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        source = QImage(source)  # shares the pixels; the ratio is set on this handle only
        source.setDevicePixelRatio(ratio)
        self._fitted, self._fitted_key = source, key
        return source

    def _paint_play_badge(self, painter: QPainter, stage: QRectF) -> None:
        """A paused (or finished) clip shows a play button in the middle."""
        size = 64.0
        rect = QRectF(stage.center().x() - size / 2, stage.center().y() - size / 2, size, size)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(GLASS_BORDER, 1))
        painter.setBrush(QColor(20, 20, 20, 150))
        painter.drawEllipse(rect)
        draw_icon(painter, "play", rect.adjusted(19, 19, -19, -19), TEXT, filled=True)

    # // mouse

    def _in_caption(self, point: QPoint) -> bool:
        if self.caption_hit is None or self.parentWidget() is None:
            return False
        return self.caption_hit(self.mapTo(self.parentWidget(), point))

    def mousePressEvent(self, event) -> None:
        self.pressed.emit()
        if event.button() == Qt.MouseButton.LeftButton and self._in_caption(event.position().toPoint()):
            self.caption_pressed.emit()
            self._press_pos = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            press, self._press_pos = self._press_pos, None
            if (event.position().toPoint() - press).manhattanLength() <= 6:
                zone = geo.seek_zone(press.x(), self.width())
                if zone:
                    # One click goes to the neighbouring item, but only once it is clear that no second
                    # click follows: a double-click skips inside the clip instead.
                    self._side_zone = zone
                    self._side_timer.start(QApplication.doubleClickInterval())
                else:
                    self._cancel_side_click()
                    self.toggle_play()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._in_caption(event.position().toPoint()):
            self.caption_double_clicked.emit()
            event.accept()
            return
        point = event.position().toPoint()
        self._cancel_side_click()
        zone = geo.seek_zone(point.x(), self.width()) if event.button() == Qt.MouseButton.LeftButton else 0
        if zone:
            self.seek_by(zone * self.seek_step_ms())  # double-click on an edge: skip 5 / 10 s
            self._press_pos = None  # its release must not count as a new single click
        else:
            self._press_pos = point  # a fast second click in the middle toggles again
        event.accept()


class SeekSlider(QWidget):
    """Thin track with a knob. Click or drag to jump; the position is only a request until the player moves."""

    seek_requested = Signal(int)  # ms

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._duration = 0
        self._position = 0
        self._dragging = False

    PAD = 8

    def set_duration(self, ms: int) -> None:
        self._duration = max(0, ms)
        self.update()

    def set_position(self, ms: int) -> None:
        if not self._dragging:
            self._position = max(0, ms)
            self.update()

    def _request(self, x: float) -> None:
        ms = geo.position_at(x, self.PAD, self.width() - 2 * self.PAD, self._duration)
        self._position = ms
        self.update()
        self.seek_requested.emit(ms)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._duration > 0:
            self._dragging = True
            self._request(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._request(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_h = 4.0
        left, right = float(self.PAD), float(self.width() - self.PAD)
        y = (self.height() - track_h) / 2
        track = QRectF(left, y, max(1.0, right - left), track_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 50))
        painter.drawRoundedRect(track, 2, 2)
        fraction = min(1.0, self._position / self._duration) if self._duration > 0 else 0.0
        accent, _ = accent_pair()
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(left, y, track.width() * fraction, track_h), 2, 2)
        knob = 12.0 if (self._dragging or self.underMouse()) else 9.0
        painter.setBrush(QColor(TEXT))
        painter.drawEllipse(QRectF(left + track.width() * fraction - knob / 2, (self.height() - knob) / 2, knob, knob))


class VolumePopup(GlassPanel):
    """Vertical volume slider that floats above the mute button while the mouse is over it."""

    level_changed = Signal(float)  # 0-1, the slider position
    hover_changed = Signal(bool)

    WIDTH = 36
    HEIGHT = 124
    PAD = 14

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, radius=18)
        # A window of its own: in the normal (GPU) mode the video widget is a native child that would
        # paint over any sibling widget reaching into the picture. Owned by the viewer, so it stays above it.
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._level = 1.0
        self._dragging = False
        self.hide()

    def set_level(self, level: float) -> None:
        self._level = min(1.0, max(0.0, level))
        self.update()

    def _request(self, y: float) -> None:
        span = max(1, self.height() - 2 * self.PAD)
        level = min(1.0, max(0.0, 1.0 - (y - self.PAD) / span))
        self._level = level
        self.update()
        self.level_changed.emit(level)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(True)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(False)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._request(event.position().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._request(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self.level_changed.emit(min(1.0, max(0.0, self._level + steps * 0.05)))
        event.accept()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_w = 4.0
        x = (self.width() - track_w) / 2
        top, bottom = float(self.PAD), float(self.height() - self.PAD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 50))
        painter.drawRoundedRect(QRectF(x, top, track_w, bottom - top), 2, 2)
        fill_h = (bottom - top) * self._level
        accent, _ = accent_pair()
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(x, bottom - fill_h, track_w, fill_h), 2, 2)
        knob = 12.0 if (self._dragging or self.underMouse()) else 9.0
        painter.setBrush(QColor(TEXT))
        painter.drawEllipse(QRectF((self.width() - knob) / 2, bottom - fill_h - knob / 2, knob, knob))


class VideoBar(GlassPanel):
    """Play / pause, elapsed time, seek slider, length and mute: the bottom pill shown for videos."""

    play_clicked = Signal()
    seek_requested = Signal(int)
    mute_clicked = Signal()
    volume_changed = Signal(float)  # 0-1, the volume slider position
    play_mode_clicked = Signal()  # cycles once -> repeat one -> in order -> shuffle
    previous_clicked = Signal()
    next_clicked = Signal()

    def __init__(self, parent: QWidget | None = None, height: int = BAR_HEIGHT):
        super().__init__(parent)
        self.setFixedHeight(height)
        side = 32
        self._side = side
        self._play = IconButton("play", side, 16, self, filled=True)
        self._play.clicked.connect(self.play_clicked)
        self._previous = IconButton("chevron-left", side, 18, self)
        self._previous.clicked.connect(self.previous_clicked)
        self._next = IconButton("chevron-right", side, 18, self)
        self._next.clicked.connect(self.next_clicked)
        self._level = 1.0
        self._skip_visible = False
        self._previous.hide()
        self._next.hide()
        self._mute = IconButton("volume", side, 17, self)
        self._mute.clicked.connect(self.mute_clicked)
        self._mute.installEventFilter(self)  # hover opens the volume slider, the wheel changes the volume
        self._volume = VolumePopup(parent)
        self._volume.level_changed.connect(self.volume_changed)
        self._volume.hover_changed.connect(self._on_volume_hover)
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(300)  # grace period so the mouse can travel from the button to the slider
        self._volume_timer.timeout.connect(self._volume.hide)
        self._mode = IconButton("arrow-right-to-line", side, 17, self, checked_bg=True)
        self._mode.setCheckable(True)
        self._mode.clicked.connect(self.play_mode_clicked)
        self._slider = SeekSlider(self)
        self._slider.seek_requested.connect(self.seek_requested)
        self._elapsed = "0:00"
        self._total = "0:00"
        self._elapsed_x = 0
        self._total_x = 0
        self.setFont(self._clock_font())
        self.hide()

    def _clock_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        return font

    def _clock_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance("0:00:00") + 6

    def set_skip_visible(self, visible: bool) -> None:
        if visible == self._skip_visible:
            return
        self._skip_visible = visible
        self._previous.setVisible(visible)
        self._next.setVisible(visible)
        self._arrange()

    def set_skip_enabled(self, previous: bool, following: bool) -> None:
        self._previous.setEnabled(previous)
        self._next.setEnabled(following)

    def set_playing(self, playing: bool) -> None:
        self._play.set_icon("pause" if playing else "play")

    def set_audio(self, level: float, muted: bool) -> None:
        silent = muted or level <= 0.0
        self._mute.set_icon("volume-off" if silent else "volume")
        self._volume.set_level(0.0 if silent else level)
        self._level = level

    def volume_popup(self) -> QWidget:
        return self._volume

    def _on_volume_hover(self, inside: bool) -> None:
        if inside:
            self._volume_timer.stop()
        else:
            self._volume_timer.start()

    def _show_volume(self) -> None:
        self._volume_timer.stop()
        self._place_volume()
        self._volume.show()
        self._volume.raise_()

    def _place_volume(self) -> None:
        spot = self._mute.mapToGlobal(QPoint(0, 0))
        x = spot.x() + (self._mute.width() - self._volume.width()) // 2
        y = self.mapToGlobal(QPoint(0, 0)).y() - self._volume.height() - 4
        self._volume.move(x, y)

    def hide_volume(self) -> None:
        self._volume_timer.stop()
        self._volume.hide()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._mute:
            kind = event.type()
            if kind == QEvent.Type.Enter:
                self._show_volume()
            elif kind == QEvent.Type.Leave:
                self._volume_timer.start()
            elif kind == QEvent.Type.Wheel:
                steps = event.angleDelta().y() / 120
                if steps:
                    self.volume_changed.emit(min(1.0, max(0.0, self._level + steps * 0.05)))
                self._show_volume()
                return True
        return super().eventFilter(obj, event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._volume.isVisible():
            self._place_volume()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._volume.hide()

    PLAY_MODES = {
        "once": ("arrow-right-to-line", "Play once"),
        "repeat": ("repeat-1", "Repeat this video"),
        "order": ("list-ordered", "Play videos in order"),
        "shuffle": ("shuffle", "Shuffle videos"),
    }

    def set_play_mode(self, mode: str) -> None:
        icon, tip = self.PLAY_MODES[mode]
        self._mode.set_icon(icon)
        self._mode.setToolTip(tip)
        self._mode.blockSignals(True)
        self._mode.setChecked(mode != "once")
        self._mode.blockSignals(False)

    def set_duration(self, ms: int) -> None:
        self._slider.set_duration(ms)
        self._total = geo.clock(ms)
        self.update()

    def set_position(self, ms: int) -> None:
        self._slider.set_position(ms)
        self._elapsed = geo.clock(ms)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self) -> None:
        side, y = self._side, (self.height() - self._side) // 2
        clock = self._clock_width()
        x = 5
        if self._skip_visible:
            self._previous.move(x, y)
            x += side + 2
        self._play.move(x, y)
        x += side + 2
        if self._skip_visible:
            self._next.move(x, y)
            x += side + 2
        self._elapsed_x = x
        x += clock
        self._mute.move(self.width() - 5 - side, y)
        self._mode.move(self.width() - 5 - 2 * side - 2, y)
        right_clock = self.width() - 5 - 2 * side - 4 - clock
        self._total_x = right_clock
        self._slider.setGeometry(x, 0, max(20, right_clock - x), self.height())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setFont(self.font())
        clock = self._clock_width()
        painter.setPen(TEXT)
        painter.drawText(QRectF(self._elapsed_x, 0, clock, self.height()), Qt.AlignmentFlag.AlignCenter, self._elapsed)
        painter.setPen(TEXT_DIM)
        painter.drawText(QRectF(self._total_x, 0, clock, self.height()), Qt.AlignmentFlag.AlignCenter, self._total)