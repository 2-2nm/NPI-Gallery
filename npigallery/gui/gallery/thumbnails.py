import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal
from PySide6.QtGui import QImage

from ... import profiling
from ...npi import build_thumbnail, build_video_thumbnail, read_record
from ...services.scanner import MediaItem
from ...services.thumbcache import ThumbnailCache
from ..common.imageconv import qimage_from_bytes


class ThumbnailSignals(QObject):
    loaded = Signal(str, QImage)
    failed = Signal(str)
    done = Signal(str)


class ThumbnailTask(QRunnable):
    def __init__(self, item: MediaItem, cache: ThumbnailCache, signals: ThumbnailSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._item = item
        self._cache = cache
        self._signals = signals

    def run(self) -> None:
        key = str(self._item.path)
        started = profiling.tick()
        try:
            try:
                image = qimage_from_bytes(self._thumbnail_bytes())
            except Exception:
                self._signals.failed.emit(key)
                return
            if image.isNull():
                self._signals.failed.emit(key)
            else:
                self._signals.loaded.emit(key, image)
        finally:
            if started:
                profiling.count("thumb_tasks")
                profiling.count("thumb_task_ms", (profiling.tick() - started) * 1000)
            self._signals.done.emit(key)

    def _thumbnail_bytes(self) -> bytes:
        path = self._item.path
        if self._item.is_npi:
            return read_record(path).thumbnail
        stat = path.stat()
        cached = self._cache.get(str(path), stat.st_mtime_ns, stat.st_size)
        if cached is not None:
            return cached
        if self._item.is_video:
            profiling.count("thumb_built_from_video")
            data = build_video_thumbnail(path)
        else:
            profiling.count("thumb_built_from_photo")
            data = build_thumbnail(path)
        self._cache.put(str(path), stat.st_mtime_ns, stat.st_size, data)
        return data


class ThumbnailLoader(QObject):
    """Pull-based loader: only a few decodes run at once and the next one is always picked
    from the *current* wish list, so work for tiles that scrolled away never piles up."""

    loaded = Signal(str, QImage)
    failed = Signal(str)

    def __init__(self, cache: ThumbnailCache, parent: QObject | None = None):
        super().__init__(parent)
        self._cache = cache
        self._queue: OrderedDict[str, MediaItem] = OrderedDict()
        self._background: OrderedDict[str, MediaItem] = OrderedDict()
        self._inflight: set[str] = set()
        self._workers = max(2, min(4, (os.cpu_count() or 4) // 2))
        self._pool = QThreadPool(self)
        # Keep the UI thread responsive: few, low-priority decoders so they do not
        # compete with painting (and the GIL) while the gallery is scrolling.
        self._pool.setMaxThreadCount(self._workers)
        if hasattr(self._pool, "setThreadPriority"):
            self._pool.setThreadPriority(QThread.Priority.LowPriority)
        self._signals = ThumbnailSignals(self)
        self._signals.loaded.connect(self.loaded)
        self._signals.failed.connect(self.failed)
        self._signals.done.connect(self._on_done)
        profiling.probe("thumb_queue", lambda: len(self._queue))
        profiling.probe("thumb_inflight", lambda: len(self._inflight))

    def enqueue(self, item: MediaItem) -> None:
        key = str(item.path)
        if key in self._inflight or key in self._queue:
            return
        self._queue[key] = item
        self._pump()

    def set_queue(self, items) -> None:
        """Replace the wish list (in priority order); anything not listed is forgotten."""
        queue: OrderedDict[str, MediaItem] = OrderedDict()
        for item in items:
            key = str(item.path)
            if key not in self._inflight:
                queue[key] = item
        self._queue = queue
        self._pump()

    def set_background(self, items) -> None:
        """Photos worth decoding when nothing on screen is waiting (they never delay the visible ones)."""
        self._background = OrderedDict(
            (str(item.path), item) for item in items if str(item.path) not in self._inflight
        )
        self._pump()

    def _pump(self) -> None:
        while len(self._inflight) < self._workers:
            source = self._queue or self._background
            if not source:
                break
            key, item = source.popitem(last=False)
            self._background.pop(key, None)
            if key in self._inflight:
                continue
            self._inflight.add(key)
            self._pool.start(ThumbnailTask(item, self._cache, self._signals))

    def _on_done(self, key: str) -> None:
        self._inflight.discard(key)
        self._pump()

    def shutdown(self) -> None:
        self._queue.clear()
        self._background.clear()
        self._pool.clear()
        self._pool.waitForDone()