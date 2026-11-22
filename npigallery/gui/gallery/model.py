import time
from collections import OrderedDict
from datetime import datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

from ... import profiling
from ...filesystem import is_under, root_keys
from ...services.albums import AlbumStore
from ...services.scanner import MediaItem
from ..common.scope import Scope, date_key, place_cell
from .thumbnails import ThumbnailLoader

PIXMAP_BYTE_LIMIT = 192 * 1024 * 1024
MIN_CACHED_PIXMAPS = 64
PREWARM_COUNT = 120  # about four screens of thumbnails (~35 MB), loaded in the background after start-up
FALLBACK_ASPECT = 1.5
MIN_ASPECT = 0.2
MAX_ASPECT = 5.0


class SortMode(Enum):
    NEWEST = "Date: newest first"
    OLDEST = "Date: oldest first"
    NAME_ASC = "Name: A to Z"
    NAME_DESC = "Name: Z to A"
    SIZE = "Size: largest first"
    SIZE_ASC = "Size: smallest first"

    @property
    def by_date(self) -> bool:
        return self in (SortMode.NEWEST, SortMode.OLDEST)


class KindFilter(Enum):
    ALL = "All items"
    ORIGINAL = "Original files"
    PACKED = "Packed in .npi"


def _pixmap_cost(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


def _day_text(timestamp: float) -> str:
    try:
        moment = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return "Unknown date"
    return f"{moment:%B} {moment.day}, {moment.year}"


def _month_key(timestamp: float) -> tuple[int, int]:
    try:
        moment = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return (0, 0)
    return (moment.year, moment.month)


class GalleryModel(QAbstractListModel):
    aspects_learned = Signal()
    favorites_changed = Signal()
    library_changed = Signal()

    def __init__(self, loader: ThumbnailLoader, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._all: list[MediaItem] = []
        self._items: list[MediaItem] = []
        self._rows: dict[str, int] = {}
        self._groups: list[tuple[str, int, int]] = []
        self._pixmaps: OrderedDict[str, QPixmap] = OrderedDict()
        self._pixmap_bytes = 0
        self._loaded_at: dict[str, float] = {}
        self._soft_reset = False
        self._failed: set[str] = set()
        self._learned: dict[str, float] = {}
        self._favorites: set[str] = set()
        self._albums = AlbumStore()
        self._album_keys: frozenset[str] = frozenset()
        self._needle = ""
        self._scope = Scope.gallery()
        self._scope_keys: tuple[tuple[str, str], ...] = ()
        self._sort = SortMode.NEWEST
        self._kind = KindFilter.ALL
        loader.loaded.connect(self._on_loaded)
        loader.failed.connect(self._on_failed)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{item.name}\n{item.path.parent}"
        return None

    def items(self) -> list[MediaItem]:
        return list(self._items)

    def item_at(self, row: int) -> MediaItem:
        return self._items[row]

    def row_for_path(self, path: Path) -> int | None:
        return self._rows.get(str(path))

    def npi_count(self) -> int:
        return sum(1 for item in self._items if item.is_npi)

    def video_count(self) -> int:
        """Videos among the items shown right now."""
        return sum(1 for item in self._items if item.is_video)

    def library_video_count(self) -> int:
        return sum(1 for item in self._all if item.is_video)

    def groups(self) -> list[tuple[str, int, int]]:
        return list(self._groups)

    def sort_mode(self) -> SortMode:
        return self._sort

    def kind_filter(self) -> KindFilter:
        return self._kind

    def scope(self) -> Scope:
        return self._scope

    def total_count(self) -> int:
        return len(self._all)

    def favorite_count(self) -> int:
        return sum(1 for item in self._all if str(item.path) in self._favorites)

    def set_items(self, items: list[MediaItem], keep_scroll: bool = False) -> None:
        self._all = list(items)
        self._failed.clear()
        self._soft_reset = keep_scroll
        with profiling.span(f"model.set_items: rebuild + view relayout ({len(items):,} photos)"):
            self._reset()
        self.library_changed.emit()

    def library(self) -> list[MediaItem]:
        return self._all

    def take_soft_reset(self) -> bool:
        """True once after a reset that should keep the view's scroll position."""
        soft, self._soft_reset = self._soft_reset, False
        return soft

    def set_scope(self, scope: Scope) -> None:
        self._scope = scope
        self._reset()

    def set_filter(self, text: str) -> None:
        self._needle = text.strip().casefold()
        self._reset()

    def set_sort(self, mode: SortMode) -> None:
        self._sort = mode
        self._reset()

    def set_kind_filter(self, kind: KindFilter) -> None:
        self._kind = kind
        self._reset()

    def loaded_age(self, item: MediaItem) -> float:
        """Seconds since this item's thumbnail arrived (large when it is long settled)."""
        stamp = self._loaded_at.get(str(item.path))
        return 999.0 if stamp is None else time.monotonic() - stamp

    def forget_pixmap(self, path: Path) -> None:
        """Drop the cached thumbnail of one photo whose file changed; it is loaded again on demand."""
        key = str(path)
        previous = self._pixmaps.pop(key, None)
        if previous is not None:
            self._pixmap_bytes -= _pixmap_cost(previous)
        self._loaded_at.pop(key, None)
        self._failed.discard(key)
        row = self.row_for_path(path)
        if row is not None:
            self.dataChanged.emit(self.index(row), self.index(row))

    def clear_pixmaps(self) -> None:
        self._loaded_at.clear()
        self._pixmaps.clear()
        self._pixmap_bytes = 0
        self._failed.clear()
        if self._items:
            self.dataChanged.emit(self.index(0), self.index(len(self._items) - 1))

    def set_favorites(self, paths: set[str]) -> None:
        self._favorites = set(paths)
        if self._scope.kind == "favorites":
            self._reset()

    def favorites(self) -> set[str]:
        return set(self._favorites)

    def is_favorite(self, item: MediaItem) -> bool:
        return str(item.path) in self._favorites

    def toggle_favorite(self, row: int) -> None:
        key = str(self._items[row].path)
        if key in self._favorites:
            self._favorites.discard(key)
        else:
            self._favorites.add(key)
        self.favorites_changed.emit()
        if self._scope.kind == "favorites":
            self._soft_reset = True
            self._reset()
        else:
            index = self.index(row)
            self.dataChanged.emit(index, index)

    def set_albums(self, albums: AlbumStore) -> None:
        self._albums = albums
        self.albums_updated()

    def albums_updated(self) -> None:
        """The album store changed in place: refill the shown album (keeping the scroll position)."""
        if self._scope.kind == "album":
            self._soft_reset = True
            self._reset()

    def aspect_for(self, item: MediaItem) -> float:
        if item.width > 0 and item.height > 0:
            ratio = item.width / item.height
        else:
            ratio = self._learned.get(str(item.path), FALLBACK_ASPECT)
        return max(MIN_ASPECT, min(MAX_ASPECT, ratio))

    def pixmap_for(self, item: MediaItem) -> QPixmap | None:
        key = str(item.path)
        pixmap = self._pixmaps.get(key)
        if pixmap is not None:
            self._pixmaps.move_to_end(key)
            return pixmap
        if key not in self._failed:
            self._loader.enqueue(item)
        return None

    def _reset(self) -> None:
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    def _accepts(self, item: MediaItem) -> bool:
        if self._needle and self._needle not in item.name.casefold():
            return False
        if self._kind is KindFilter.ORIGINAL and item.is_npi:
            return False
        if self._kind is KindFilter.PACKED and not item.is_npi:
            return False
        scope = self._scope
        if scope.kind == "photos":
            return not item.is_video
        if scope.kind == "videos":
            return item.is_video
        if scope.kind == "favorites":
            return str(item.path) in self._favorites
        if scope.kind == "album":
            return str(item.path) in self._album_keys
        if scope.kind == "folder" and scope.path is not None:
            return is_under(str(item.path), self._scope_keys)
        if scope.kind == "date":
            stamp = date_key(item.sort_time)
            if stamp is None or stamp[0] != scope.year:
                return False
            return not scope.month or stamp[1] == scope.month
        if scope.kind == "place":
            return place_cell(item.latitude, item.longitude) == scope.cell
        return True

    def items_under(self, folder: Path) -> list[MediaItem]:
        """Every known item inside `folder`, in the current sort order (ignores scope and filters)."""
        keys = root_keys([folder])
        return self._sorted([item for item in self._all if is_under(str(item.path), keys)])

    def prewarm(self, count: int = PREWARM_COUNT) -> None:
        """Decode the first screens in the background, so a scroll right after start-up finds them ready."""
        self._loader.set_background(
            item
            for item in self._items[:count]
            if str(item.path) not in self._pixmaps and str(item.path) not in self._failed
        )

    def _rebuild(self) -> None:
        scope = self._scope
        self._scope_keys = root_keys([scope.path]) if scope.kind == "folder" and scope.path is not None else ()
        self._album_keys = self._albums.paths(scope.name) if scope.kind == "album" else frozenset()
        selected = self._sorted([item for item in self._all if self._accepts(item)])
        mode = self._sort
        self._items = selected
        self._rows = {str(item.path): row for row, item in enumerate(selected)}
        self._groups = self._build_groups() if mode.by_date else []

    def _sorted(self, selected: list[MediaItem]) -> list[MediaItem]:
        mode = self._sort
        if mode is SortMode.NEWEST:
            selected.sort(key=lambda item: item.sort_time, reverse=True)
        elif mode is SortMode.OLDEST:
            selected.sort(key=lambda item: item.sort_time)
        elif mode is SortMode.NAME_ASC:
            selected.sort(key=lambda item: item.name.casefold())
        elif mode is SortMode.NAME_DESC:
            selected.sort(key=lambda item: item.name.casefold(), reverse=True)
        elif mode is SortMode.SIZE_ASC:
            selected.sort(key=lambda item: item.size)
        else:
            selected.sort(key=lambda item: item.size, reverse=True)
        return selected

    def _build_groups(self) -> list[tuple[str, int, int]]:
        groups: list[tuple[str, int, int]] = []
        start = 0
        for row in range(1, len(self._items) + 1):
            at_end = row == len(self._items)
            if not at_end and _month_key(self._items[row].sort_time) == _month_key(
                self._items[start].sort_time
            ):
                continue
            first = _day_text(self._items[start].sort_time)
            last = _day_text(self._items[row - 1].sort_time)
            groups.append((first if first == last else self._range_label(first, last), start, row))
            start = row
        return groups

    def _range_label(self, first: str, last: str) -> str:
        if self._sort is SortMode.NEWEST:
            return f"{last} - {first}"
        return f"{first} - {last}"

    def _on_loaded(self, key: str, image: QImage) -> None:
        profiling.count("thumb_loaded")
        previous = self._pixmaps.pop(key, None)
        if previous is not None:
            self._pixmap_bytes -= _pixmap_cost(previous)
        pixmap = QPixmap.fromImage(image)
        self._pixmaps[key] = pixmap
        self._loaded_at[key] = time.monotonic()
        self._pixmap_bytes += _pixmap_cost(pixmap)
        while self._pixmap_bytes > PIXMAP_BYTE_LIMIT and len(self._pixmaps) > MIN_CACHED_PIXMAPS:
            evicted_key, evicted = self._pixmaps.popitem(last=False)
            self._loaded_at.pop(evicted_key, None)
            self._pixmap_bytes -= _pixmap_cost(evicted)
        row = self._rows.get(key)
        if row is None:
            return
        item = self._items[row]
        if (item.width <= 0 or item.height <= 0) and key not in self._learned and image.height() > 0:
            self._learned[key] = image.width() / image.height()
            self.aspects_learned.emit()
        index = self.index(row)
        self.dataChanged.emit(index, index)

    def set_wanted_rows(self, first: int, last: int, focus_first: int, focus_last: int) -> None:
        """Tell the loader what to decode next: the visible rows first, then the margin."""
        count = len(self._items)
        first, last = max(0, first), min(count - 1, last)
        focus_first, focus_last = max(first, focus_first), min(last, focus_last)
        order = list(range(focus_first, focus_last + 1))
        order += list(range(focus_first - 1, first - 1, -1)) + list(range(focus_last + 1, last + 1))
        self._loader.set_queue(
            self._items[row]
            for row in order
            if str(self._items[row].path) not in self._pixmaps and str(self._items[row].path) not in self._failed
        )

    def _on_failed(self, key: str) -> None:
        self._failed.add(key)