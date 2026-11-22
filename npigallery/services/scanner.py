import os
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..npi import NpiError, is_image_path, is_npi_path, is_video_path, read_image_facts, read_record, read_video_facts
from ..npi.imaging import read_coordinates


SCAN_THREADS = 8
SCAN_CHUNK = 48
PROGRESS_INTERVAL = 0.7
FIRST_PROGRESS_DELAY = 0.6  # first snapshot after ~0.1 s


@dataclass(frozen=True)
class MediaItem:
    path: Path
    name: str
    is_npi: bool
    size: int
    sort_time: float
    format_label: str
    width: int = 0
    height: int = 0
    mtime_ns: int = 0
    file_size: int = 0
    latitude: float | None = None
    longitude: float | None = None
    is_video: bool = False
    duration: float = 0.0  # seconds; videos only


def _normalize_excluded(excluded: Iterable[Path]) -> tuple[str, ...]:
    keys: list[str] = []
    for path in excluded:
        norm = os.path.normcase(os.path.abspath(str(path)))
        if norm not in keys:
            keys.append(norm)
    return tuple(keys)


def _is_excluded(path: Path | str, excluded_keys: tuple[str, ...]) -> bool:
    if not excluded_keys:
        return False
    norm = os.path.normcase(os.path.abspath(str(path)))
    return any(norm == key or norm.startswith(key + os.sep) for key in excluded_keys)


def is_media_path(path: Path) -> bool:
    return is_image_path(path) or is_video_path(path) or is_npi_path(path)


def iter_media_paths(
    root: Path,
    recursive: bool = True,
    excluded: Iterable[Path] = (),
) -> Iterator[Path]:
    excluded_keys = _normalize_excluded(excluded)
    if _is_excluded(root, excluded_keys):
        return
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive and not _is_excluded(entry.path, excluded_keys):
                            pending.append(Path(entry.path))
                        continue
                    path = Path(entry.path)
                    if is_media_path(path):
                        yield path
        except OSError:
            continue


def has_media(
    root: Path,
    cache: dict[str, bool] | None = None,
    pause: Callable[[], None] | None = None,
    excluded: Iterable[Path] = (),
) -> bool:
    """True if `root` or any folder below it holds at least one photo (stops at the first).

    `pause` is called before each folder is read, so a background caller can yield to the UI thread.
    """
    excluded_keys = _normalize_excluded(excluded)
    if _is_excluded(root, excluded_keys):
        return False
    key = str(root)
    if cache is not None and key in cache:
        return cache[key]
    if pause is not None:
        pause()
    found = False
    subfolders: list[Path] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if not entry.name.startswith(".") and not _is_excluded(entry.path, excluded_keys):
                        subfolders.append(Path(entry.path))
                    continue
                path = Path(entry.path)
                if is_media_path(path):
                    found = True
                    break
    except OSError:
        pass
    if not found:
        found = any(has_media(folder, cache, pause, excluded) for folder in subfolders)
    if cache is not None:
        cache[key] = found
    return found


def media_subfolders(
    root: Path,
    cache: dict[str, bool] | None = None,
    pause: Callable[[], None] | None = None,
    excluded: Iterable[Path] = (),
) -> list[Path]:
    """Direct subfolders of `root` that contain photos somewhere inside, sorted by name."""
    excluded_keys = _normalize_excluded(excluded)
    if _is_excluded(root, excluded_keys):
        return []
    try:
        with os.scandir(root) as entries:
            folders = [
                Path(entry.path)
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
                and not entry.name.startswith(".")
                and not _is_excluded(entry.path, excluded_keys)
            ]
    except OSError:
        return []
    folders.sort(key=lambda path: path.name.casefold())
    return [folder for folder in folders if has_media(folder, cache, pause, excluded)]


def describe_media(path: Path, known: MediaItem | None = None) -> MediaItem | None:
    try:
        stat = path.stat()
        if (
            known is not None
            and known.mtime_ns == stat.st_mtime_ns
            and known.file_size == stat.st_size
        ):
            return known  # unchanged since the last scan: skip opening the file
        if is_npi_path(path):
            record = read_record(path)
            meta = record.meta
            coordinates = read_coordinates(record.exif)
            sort_time = meta.taken_at if meta.taken_at is not None else meta.mtime_ns / 1e9
            return MediaItem(
                path,
                meta.original_name,
                True,
                meta.size,
                sort_time,
                meta.image_format.name,
                meta.width,
                meta.height,
                stat.st_mtime_ns,
                stat.st_size,
                coordinates[0] if coordinates else None,
                coordinates[1] if coordinates else None,
            )
        if is_video_path(path):
            width, height, taken_at, duration, label = read_video_facts(path)
            if width <= 0 or height <= 0:
                return None  # unreadable, or PyAV is not installed
            return MediaItem(
                path,
                path.name,
                False,
                stat.st_size,
                taken_at if taken_at is not None else stat.st_mtime,
                label,
                width,
                height,
                stat.st_mtime_ns,
                stat.st_size,
                None,
                None,
                True,
                duration,
            )
        width, height, taken_at, coordinates, label = read_image_facts(path)
        if width <= 0 or height <= 0:
            return None  # unreadable or corrupt: would only show as an empty tile
        return MediaItem(
            path,
            path.name,
            False,
            stat.st_size,
            taken_at if taken_at is not None else stat.st_mtime,
            label,
            width,
            height,
            stat.st_mtime_ns,
            stat.st_size,
            coordinates[0] if coordinates else None,
            coordinates[1] if coordinates else None,
        )
    except (OSError, NpiError):
        return None


def scan_folder(
    root: Path,
    recursive: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    known: dict[str, MediaItem] | None = None,
    excluded: Iterable[Path] = (),
) -> list[MediaItem]:
    paths = []
    for path in iter_media_paths(root, recursive, excluded=excluded):
        if should_cancel is not None and should_cancel():
            return []
        paths.append(path)
    known = known or {}

    def describe(path: Path) -> MediaItem | None:
        if should_cancel is not None and should_cancel():
            return None
        return describe_media(path, known.get(str(path)))

    if len(paths) > 32:
        # Header reads are mostly I/O latency, so threads speed up a cold scan a lot.
        with ThreadPoolExecutor(max_workers=SCAN_THREADS) as pool:
            described = list(pool.map(describe, paths))
    else:
        described = [describe(path) for path in paths]
    items = [item for item in described if item is not None]
    items.sort(key=lambda item: item.sort_time, reverse=True)
    return items


def scan_folders(
    roots: list[Path],
    recursive: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    known: dict[str, MediaItem] | None = None,
    on_progress: Callable[[list[MediaItem]], None] | None = None,
    workers: int = SCAN_THREADS,
    excluded: Iterable[Path] = (),
) -> list[MediaItem]:
    """Scan every root. `on_progress` receives a growing, sorted snapshot while files
    are still being read, so a cold start can fill the gallery within ~100 ms."""
    paths: dict[Path, None] = {}
    for root in roots:
        for path in iter_media_paths(root, recursive, excluded=excluded):
            if should_cancel is not None and should_cancel():
                return []
            paths.setdefault(path)
    known = known or {}

    def describe(path: Path) -> MediaItem | None:
        if should_cancel is not None and should_cancel():
            return None
        return describe_media(path, known.get(str(path)))

    found: list[MediaItem] = []
    last_emit = time.monotonic() - PROGRESS_INTERVAL + FIRST_PROGRESS_DELAY
    ordered = list(paths)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for start in range(0, len(ordered), SCAN_CHUNK):
            if should_cancel is not None and should_cancel():
                return []
            chunk = ordered[start : start + SCAN_CHUNK]
            found.extend(item for item in pool.map(describe, chunk) if item is not None)
            now = time.monotonic()
            if on_progress is not None and now - last_emit >= PROGRESS_INTERVAL and start + SCAN_CHUNK < len(ordered):
                last_emit = now
                on_progress(sorted(found, key=lambda item: item.sort_time, reverse=True))
    return sorted(found, key=lambda item: item.sort_time, reverse=True)