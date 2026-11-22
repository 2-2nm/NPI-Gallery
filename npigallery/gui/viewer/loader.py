"""Background decoding for the viewer: the photo on screen plus its neighbours, off the UI thread."""

import io
import os
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from ...formatting import human_size
from ...npi import (
    inspect_image,
    read_exif_tags,
    read_gps,
    read_image_bytes,
    read_meta,
)
from ...npi.formats import ImageFormat, detect_format
from ...npi.imaging import load_oriented_display
from ...services.scanner import MediaItem
from ..common.imageconv import qimage_from_display
from .cachepolicy import eviction_order
from .geometry import bit_depth

CACHE_SIZE = 5
CACHE_BYTE_LIMIT = 192 * 1024 * 1024  # decoded photos kept besides the ones on screen / prefetched
THREADS = 3


@dataclass
class ViewData:
    path: str
    image: QImage
    width: int
    height: int
    file_size: int
    dpi: str
    depth: str
    taken_at: float | None
    sections: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    # The encoded file of an animated GIF. `image` then holds only the first frame (copy, print,
    # placeholder); the canvas plays the animation from these bytes.
    animation: bytes | None = None


def decode(item: MediaItem, with_location: bool) -> ViewData:
    data = read_image_bytes(item.path)
    file_size = len(data)
    info = inspect_image(data)
    dpi, depth = "-", "-"
    animated = False
    try:
        with Image.open(io.BytesIO(data)) as source:
            raw = source.info.get("dpi")
            if raw and raw[0]:
                dpi = f"{round(float(raw[0]))} dpi"
            depth = bit_depth(source.mode)
            animated = detect_format(data) is ImageFormat.GIF and bool(getattr(source, "is_animated", False))
    except Exception:
        pass
    image = qimage_from_display(load_oriented_display(io.BytesIO(data)))
    animation = data if animated else None
    del data  # a still photo does not need its file bytes once the pixels exist
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    if item.is_npi:
        meta = read_meta(item.path)
        sections.append(
            (
                "NPI container",
                [
                    ("Original format", meta.image_format.name),
                    ("Container size", human_size(os.path.getsize(item.path))),
                ],
            )
        )
    sections.append(("EXIF", read_exif_tags(info.exif)))
    if with_location:
        sections.append(("Location", read_gps(info.exif)))
    return ViewData(
        path=str(item.path),
        image=image,
        width=info.width,
        height=info.height,
        file_size=file_size,
        dpi=dpi,
        depth=depth,
        taken_at=info.taken_at,
        sections=sections,
        animation=animation,
    )


class _Signals(QObject):
    loaded = Signal(str, object)
    failed = Signal(str, str)
    finished = Signal(str, bool)  # key, whether the task actually ran


class _Task(QRunnable):
    def __init__(self, loader: "ViewerLoader", item: MediaItem, with_location: bool):
        super().__init__()
        self.setAutoDelete(True)
        self._loader = loader
        self._item = item
        self._with_location = with_location

    def run(self) -> None:
        key = str(self._item.path)
        signals = self._loader.signals
        ran = False
        try:
            if key not in self._loader.wanted_keys():
                return
            ran = True
            try:
                data = decode(self._item, self._with_location)
            except Exception as error:
                signals.failed.emit(key, str(error))
            else:
                signals.loaded.emit(key, data)
        except RuntimeError:
            pass  # the viewer was closed while we were decoding
        finally:
            try:
                signals.finished.emit(key, ran)
            except RuntimeError:
                pass


class ViewerLoader(QObject):
    """`want([...])` names what is needed, current photo first; everything else is dropped.
    Results arrive through `loaded` / `failed`; finished ones are kept in a small LRU cache."""

    loaded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None, with_location: bool = False):
        super().__init__(parent)
        self.signals = _Signals(self)
        self.signals.loaded.connect(self._on_loaded)
        self.signals.failed.connect(self.failed)
        self.signals.finished.connect(self._on_finished)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(THREADS)
        self._cache: OrderedDict[str, ViewData] = OrderedDict()
        self._wanted: dict[str, MediaItem] = {}
        self._inflight: set[str] = set()
        self._with_location = with_location
        self._closed = False

    def wanted_keys(self) -> set[str]:
        return set(self._wanted)

    def get(self, item: MediaItem) -> ViewData | None:
        key = str(item.path)
        data = self._cache.get(key)
        if data is not None:
            self._cache.move_to_end(key)
        return data

    def forget(self, item: MediaItem) -> None:
        self._cache.pop(str(item.path), None)

    def renamed(self, old: MediaItem, new: MediaItem) -> None:
        """Keep the decoded photo when its file was only renamed."""
        data = self._cache.pop(str(old.path), None)
        if data is not None:
            self._cache[str(new.path)] = replace(data, path=str(new.path))

    def want(self, items: list[MediaItem]) -> None:
        if self._closed:
            return
        self._wanted = {str(item.path): item for item in items}
        for position, item in enumerate(items):
            self._start(item, priority=2 if position == 0 else 0)

    def _start(self, item: MediaItem, priority: int) -> None:
        key = str(item.path)
        if self._closed or key in self._cache or key in self._inflight:
            return
        self._inflight.add(key)
        self._pool.start(_Task(self, item, self._with_location), priority)

    def _on_loaded(self, key: str, data: ViewData) -> None:
        if self._closed:
            return  # a decode that finished while the viewer was closing: do not keep its pixels
        self._cache[key] = data
        self._cache.move_to_end(key)
        self._trim()
        self.loaded.emit(key, data)

    def _trim(self) -> None:
        """Keep the cache within CACHE_SIZE photos and CACHE_BYTE_LIMIT bytes (least recent go first)."""
        sizes = {key: view.image.sizeInBytes() + len(view.animation or b"") for key, view in self._cache.items()}
        for key in eviction_order(sizes, self._wanted, CACHE_SIZE, CACHE_BYTE_LIMIT):
            del self._cache[key]

    def _on_finished(self, key: str, ran: bool) -> None:
        self._inflight.discard(key)
        if not ran:
            # skipped because it was not wanted then; it may be wanted again by now
            item = self._wanted.get(key)
            if item is not None:
                self._start(item, priority=2)

    def release(self) -> None:
        """Drop every decoded photo and refuse new ones. After this the loader holds no pixels."""
        self._closed = True
        self._wanted.clear()
        self._inflight.clear()
        self._cache.clear()

    def shutdown(self) -> None:
        self._wanted.clear()
        self._pool.clear()
        self._pool.waitForDone(3000)
        self.release()  # after the wait: a decode that just finished must not refill the cache