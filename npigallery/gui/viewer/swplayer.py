"""Software video playback (PyAV + QAudioSink) for clips QtMultimedia cannot decode on this machine.

QtMultimedia's FFmpeg build only decodes AV1 on a GPU that supports it; without such a GPU the clip simply
fails. PyAV ships its own FFmpeg with the dav1d decoder, so this player decodes on the CPU instead.

    worker thread   demux + decode with PyAV, scale each frame to the size it will have on screen
                    (swscale, bicubic) and queue it; decode audio to 48 kHz stereo PCM and queue that
    GUI thread      a short timer feeds the PCM to a QAudioSink and shows the video frame that is due

The audio device is the clock (what it has actually played), so picture and sound stay together; a clip
without sound uses the wall clock. All Qt objects live on the GUI thread; the worker only touches Python
objects, the two queues (under one lock) and plain QImage values.

`VideoSurface` uses it as a fallback: when QtMultimedia reports an error, or an AV1 clip shows no picture.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QTransform
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from ...npi import video as npi_video

AUDIO_RATE = 48000
BYTES_PER_SECOND = AUDIO_RATE * 2 * 2  # stereo, 16 bit
SOFT_VIDEO_FRAMES = 10  # decoding pauses when this many frames wait and the audio has enough, ...
HARD_VIDEO_FRAMES = 24  # ... and always at this many
SOFT_AUDIO_BYTES = BYTES_PER_SECOND  # one second of sound
HARD_AUDIO_BYTES = 4 * BYTES_PER_SECOND
PUMP_MS = 4
POSITION_EVERY_MS = 100
DEBUG = os.environ.get("NPI_VIDEO_DEBUG") == "1"  # prints what the player does to stderr


def _dbg(message: str) -> None:
    if DEBUG:
        print(f"[npigallery sw-video] {message}", file=sys.stderr, flush=True)


def available() -> bool:
    """True when PyAV is installed (it is what decodes here)."""
    return npi_video.video_support_available()


def video_codec_name(path) -> str:
    """The codec of the first video stream ('av1', 'h264', ...), or '' when it cannot be told."""
    try:
        av = npi_video._load_av()
        with av.open(str(path)) as container:
            stream = npi_video._video_stream(container)
            return (stream.codec_context.name or "") if stream is not None else ""
    except Exception:
        return ""


def _software_decoder(av, vstream):
    """A decoder context to use instead of the stream's own, or None when the stream's own is fine.

    For AV1, FFmpeg's built-in decoder ("av1") only works through a GPU; the software ones are the external
    libraries dav1d and libaom, and a stream opened by PyAV gets the built-in one unless it is chosen by name.
    Raises when the clip is AV1 and this FFmpeg has neither library.
    """
    if vstream.codec_context.name != "av1":
        return None
    default = getattr(getattr(vstream.codec_context, "codec", None), "name", "")
    if default not in ("av1", ""):
        return None  # FFmpeg already picked libdav1d / libaom
    from av.codec.context import CodecContext

    for name in ("libdav1d", "libaom-av1"):
        try:
            context = CodecContext.create(name, "r")
        except Exception:
            continue
        try:
            context.extradata = vstream.codec_context.extradata
        except Exception:
            pass
        try:
            context.thread_type = "AUTO"
        except Exception:
            pass
        _dbg(f"AV1: using the software decoder {name}")
        return context
    present = sorted(c for c in av.codecs_available if "av1" in c or "dav1d" in c or "aom" in c)
    raise RuntimeError(
        "This FFmpeg (PyAV) has no software AV1 decoder: it needs libdav1d or libaom, "
        f"found only {present}. Try: pip install -U av"
    )


class SoftwarePlayer(QObject):
    frame_ready = Signal(object)  # a QImage already scaled to fit the stage
    position_changed = Signal(int)  # ms
    duration_changed = Signal(int)  # ms
    playing_changed = Signal(bool)
    ended = Signal()
    failed = Signal(str)

    _opened = Signal(int, int, bool)  # session, duration ms, has audio (worker -> GUI)
    _error = Signal(int, str)  # session, message (worker -> GUI)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._session = 0  # bumped by open() and close(): a worker of an older session stops
        self._epoch = 0  # bumped by every seek: frames decoded for an older position are dropped
        self._seek_request: int | None = None
        self._video: collections.deque = collections.deque()  # (pts ms, QImage)
        self._audio: collections.deque = collections.deque()  # (pts ms, bytes)
        self._audio_bytes = 0
        self._eof = False
        self._target = (0, 0)  # the size, in device pixels, the picture has to fit
        self._has_audio = False
        self._duration = 0
        self._loop = False
        self._playing = False
        self._finished = False
        self._need_first = False  # show the next frame at once (after opening and after a seek)
        self._position = 0
        self._sink: QAudioSink | None = None
        self._device = None
        self._audio_base: float | None = None  # pts of the first sound written since the sink started
        self._base_ms = 0.0  # where the wall clock started from
        self._wall_start: float | None = None
        self._wall_acc = 0.0
        self._volume = 1.0
        self._muted = False
        self._last_position_emit = 0.0
        self._opened.connect(self._on_opened)
        self._error.connect(self._on_error)
        self._pump = QTimer(self)
        self._pump.setTimerType(Qt.TimerType.PreciseTimer)
        self._pump.setInterval(PUMP_MS)
        self._pump.timeout.connect(self._tick)

    # // control (GUI thread)

    def open(self, path: Path, autoplay: bool = True) -> None:
        self.close()
        with self._cv:
            self._session += 1
            session = self._session
            self._epoch += 1
            self._seek_request = None
            self._video.clear()
            self._audio.clear()
            self._audio_bytes = 0
            self._eof = False
        self._finished = False
        self._need_first = True
        self._position = 0
        self._base_ms, self._wall_acc, self._wall_start = 0.0, 0.0, None
        self._audio_base = None
        self._has_audio = False
        self._thread = threading.Thread(target=self._work, args=(session, str(path)), name="npi-sw-video", daemon=True)
        self._thread.start()
        self._pump.start()
        if autoplay:
            self.play()

    def close(self) -> None:
        with self._cv:
            self._session += 1
            self._video.clear()
            self._audio.clear()
            self._audio_bytes = 0
            self._cv.notify_all()
        self._pump.stop()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(2.0)  # it notices the new session within one packet; the file is closed by then
        self._drop_sink()
        self._playing = False
        self._wall_start = None

    def is_playing(self) -> bool:
        return self._playing

    def duration(self) -> int:
        return self._duration

    def position(self) -> int:
        return int(self._clock()) if self._playing else int(self._position)

    def set_loop(self, on: bool) -> None:
        self._loop = bool(on)

    def set_volume(self, level: float) -> None:
        self._volume = min(1.0, max(0.0, level))
        self._apply_volume()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._apply_volume()

    def set_target(self, width: int, height: int) -> None:
        """The stage size in device pixels; frames decoded from now on are scaled to fit it."""
        self._target = (max(2, int(width)), max(2, int(height)))

    def play(self) -> None:
        if self._playing:
            return
        if self._finished:
            self.seek(0)  # from the start again when the clip had ended
        self._playing = True
        self._wall_start = time.monotonic()
        if self._sink is not None and self._device is not None:
            self._sink.resume()
        self.playing_changed.emit(True)

    def pause(self) -> None:
        if not self._playing:
            return
        self._position = int(self._clock())
        if self._wall_start is not None:
            self._wall_acc += (time.monotonic() - self._wall_start) * 1000.0
            self._wall_start = None
        self._playing = False
        if self._sink is not None and self._device is not None:
            self._sink.suspend()
        self.playing_changed.emit(False)

    def seek(self, ms: int) -> None:
        ms = max(0, int(ms))
        if self._duration:
            ms = min(ms, self._duration)
        with self._cv:
            self._epoch += 1
            self._seek_request = ms
            self._video.clear()
            self._audio.clear()
            self._audio_bytes = 0
            self._eof = False
            self._cv.notify_all()
        self._finished = False
        self._need_first = True
        self._position = ms
        self._base_ms, self._wall_acc = float(ms), 0.0
        self._wall_start = time.monotonic() if self._playing else None
        self._audio_base = None
        if self._sink is not None:
            self._sink.stop()  # restarts (and its played-time counter with it) when the next sound is written
            self._device = None
        self.position_changed.emit(ms)

    # // clock and pump (GUI thread)

    def _clock(self) -> float:
        if self._has_audio and self._sink is not None:
            if self._audio_base is None:
                return self._base_ms  # the sound has not started yet: the picture waits for it
            return self._audio_base + self._sink.processedUSecs() / 1000.0
        running = (time.monotonic() - self._wall_start) * 1000.0 if self._wall_start is not None else 0.0
        return self._base_ms + self._wall_acc + running

    def _tick(self) -> None:
        if self._playing and self._sink is not None:
            self._feed_audio()
        shown = None
        with self._cv:
            if self._need_first:
                if self._video:
                    shown = self._video.popleft()
                    self._need_first = False
            elif self._playing:
                now = self._clock()
                while self._video and self._video[0][0] <= now:
                    shown = self._video.popleft()  # a frame that is late is skipped for the next one
            if shown is not None:
                self._cv.notify_all()
        if shown is not None:
            if self._position == 0 and DEBUG:
                _dbg(f"showing frame {shown[0]:.0f}ms ({shown[1].width()}x{shown[1].height()})")
            self._position = shown[0]
            self.frame_ready.emit(shown[1])
        if self._playing:
            now_s = time.monotonic()
            if (now_s - self._last_position_emit) * 1000.0 >= POSITION_EVERY_MS:
                self._last_position_emit = now_s
                self.position_changed.emit(min(int(self._clock()), self._duration or int(self._clock())))
            self._check_end()

    def _feed_audio(self) -> None:
        sink = self._sink
        if sink is None:
            return
        if self._device is None:
            with self._cv:
                if not self._audio:
                    return
            self._device = sink.start()
            self._apply_volume()
            if self._device is None:
                return
        free = sink.bytesFree()
        while free > 0:
            with self._cv:
                if not self._audio:
                    break
                pts, data = self._audio[0]
                count = min(free, len(data))
                chunk = data[:count]
                if count == len(data):
                    self._audio.popleft()
                else:
                    self._audio[0] = (pts + count * 1000.0 / BYTES_PER_SECOND, data[count:])
                self._audio_bytes -= count
                self._cv.notify_all()
            written = self._device.write(chunk)
            if self._audio_base is None:
                self._audio_base = pts
            if written is None or written <= 0:
                break
            free -= count

    def _check_end(self) -> None:
        with self._cv:
            done = self._eof and not self._video and not self._audio
        if not done:
            return
        sink = self._sink
        if sink is not None and self._device is not None:
            size = sink.bufferSize()
            if size > 0 and sink.bytesFree() < size:
                return  # the last sound is still playing
        if self._loop:
            self.seek(0)
            return
        self._playing = False
        self._finished = True
        self._wall_start = None
        self._wall_acc = 0.0
        self.position_changed.emit(self._duration)
        self.playing_changed.emit(False)
        self.ended.emit()

    def _apply_volume(self) -> None:
        if self._sink is not None:
            self._sink.setVolume(0.0 if self._muted else self._volume)

    def _drop_sink(self) -> None:
        sink, self._sink = self._sink, None
        self._device = None
        if sink is not None:
            sink.stop()
            sink.deleteLater()

    def _on_opened(self, session: int, duration_ms: int, has_audio: bool) -> None:
        if session != self._session:
            return
        self._duration = duration_ms
        self.duration_changed.emit(duration_ms)
        self._has_audio = has_audio
        if has_audio:
            fmt = QAudioFormat()
            fmt.setSampleRate(AUDIO_RATE)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            device = QMediaDevices.defaultAudioOutput()
            _dbg(f"audio output: {device.description()!r} null={device.isNull()}")
            self._sink = QAudioSink(device, fmt, self)
            self._apply_volume()

    def _on_error(self, session: int, message: str) -> None:
        if session == self._session:
            self.failed.emit(message)

    # // worker thread

    def _full(self) -> bool:
        videos = len(self._video)
        if videos >= HARD_VIDEO_FRAMES or self._audio_bytes >= HARD_AUDIO_BYTES:
            return True
        return videos >= SOFT_VIDEO_FRAMES and (not self._has_audio or self._audio_bytes >= SOFT_AUDIO_BYTES)

    def _work(self, session: int, path: str) -> None:
        container = None
        try:
            av = npi_video._load_av()
            container = av.open(path)
            vstream = npi_video._video_stream(container)
            if vstream is None:
                raise ValueError("The file has no video stream")
            vstream.thread_type = "AUTO"
            decoder = getattr(getattr(vstream.codec_context, "codec", None), "name", "?")
            forced = _software_decoder(av, vstream)  # None unless AV1 would end up on the hardware-only decoder
            if forced is not None:
                decoder = getattr(getattr(forced, "codec", None), "name", decoder)
            astream = container.streams.audio[0] if container.streams.audio else None
            resampler = av.AudioResampler(format="s16", layout="stereo", rate=AUDIO_RATE) if astream else None
            rotation = npi_video._clockwise_rotation(container, vstream)
            duration_ms = int(npi_video._duration_seconds(container, vstream) * 1000)
            _dbg(
                f"open: codec={vstream.codec_context.name} decoder={decoder} {vstream.codec_context.width}x"
                f"{vstream.codec_context.height} duration={duration_ms}ms audio={astream is not None} rotation={rotation}"
            )
            self._opened.emit(session, duration_ms, astream is not None)
            decoded = errors = 0
            last_error = ""
            streams = [vstream] + ([astream] if astream is not None else [])
            demux = container.demux(*streams)
            epoch = self._epoch
            skip_until = 0.0
            while True:
                with self._cv:
                    while True:
                        if session != self._session:
                            return
                        if self._seek_request is not None:
                            break
                        if not self._eof and not self._full():
                            break
                        self._cv.wait(0.05)
                    seek, self._seek_request = self._seek_request, None
                    epoch = self._epoch
                if seek is not None:
                    try:
                        container.seek(int(seek * 1000), backward=True, any_frame=False)
                    except Exception:
                        pass  # not seekable: carry on from wherever the container is
                    demux = container.demux(*streams)
                    skip_until = float(seek)
                try:
                    packet = next(demux)
                except StopIteration:
                    with self._cv:
                        if epoch == self._epoch:
                            self._eof = True
                    continue
                except Exception:
                    with self._cv:
                        if epoch == self._epoch:
                            self._eof = True
                    continue
                is_video = packet.stream.type == "video"
                try:
                    frames = forced.decode(packet) if forced is not None and is_video else packet.decode()
                except Exception as error:
                    errors += 1
                    last_error = str(error)
                    _dbg(f"decode error #{errors}: {last_error}")
                    if decoded == 0 and errors >= 10:
                        # Nothing has ever decoded: this is not a damaged packet, the decoder cannot work here.
                        raise RuntimeError(f"Cannot decode this video ({decoder}): {last_error}") from error
                    continue  # a damaged packet: skip it
                for frame in frames:
                    moment = frame.time
                    ms = float(moment) * 1000.0 if moment is not None else skip_until
                    if is_video:
                        if ms < skip_until - 1.0:
                            continue  # between the keyframe and the wanted position
                        image = self._to_image(frame, rotation)
                        if image is None:
                            continue
                        decoded += 1
                        if decoded == 1:
                            _dbg(f"first frame decoded: {image.width()}x{image.height()} at {ms:.0f}ms")
                        with self._cv:
                            if epoch != self._epoch or session != self._session:
                                break
                            self._video.append((ms, image))
                    elif resampler is not None:
                        out = resampler.resample(frame)
                        for piece in out if isinstance(out, list) else [out]:
                            if piece is None:
                                continue
                            data = bytes(piece.planes[0])[: piece.samples * 4]
                            if ms + piece.samples * 1000.0 / AUDIO_RATE < skip_until:
                                continue
                            with self._cv:
                                if epoch != self._epoch or session != self._session:
                                    break
                                self._audio.append((ms, data))
                                self._audio_bytes += len(data)
        except Exception as error:
            if DEBUG:
                traceback.print_exc()
            if session == self._session:
                self._error.emit(session, str(error) or "Unsupported video")
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass

    def _to_image(self, frame, rotation: int) -> QImage | None:
        """The frame as an RGB QImage scaled to fit the stage (bicubic, by swscale), turned upright."""
        width, height = frame.width, frame.height
        if not width or not height:
            return None
        sideways = rotation in (90, 270)
        shown_w, shown_h = (height, width) if sideways else (width, height)
        target_w, target_h = self._target
        if target_w <= 2 or target_h <= 2:
            scale = 1.0
        else:
            scale = min(target_w / shown_w, target_h / shown_h)
        out_w, out_h = max(2, round(shown_w * scale)), max(2, round(shown_h * scale))
        decode_w, decode_h = (out_h, out_w) if sideways else (out_w, out_h)
        try:
            rgb = frame.reformat(width=decode_w, height=decode_h, format="rgb24", interpolation="BICUBIC")
        except Exception:
            rgb = frame.reformat(width=decode_w, height=decode_h, format="rgb24")
        plane = rgb.planes[0]
        image = QImage(bytes(plane), decode_w, decode_h, plane.line_size, QImage.Format.Format_RGB888).copy()
        if rotation:
            image = image.transformed(QTransform().rotate(rotation))
        return image