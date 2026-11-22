from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ... import profiling
from ...services.index import MediaIndex
from ...services.scanner import SCAN_THREADS, MediaItem, scan_folders
from ...services.thumbcache import ThumbnailCache


SCAN_WORKERS_REFRESH = 2


class ScanWorker(QThread):
    scanned = Signal(object)
    partial = Signal(object)

    def __init__(
        self,
        roots: list[Path],
        recursive: bool,
        index: MediaIndex | None = None,
        known: list[MediaItem] | None = None,
        progressive: bool = False,
        thumbnails: ThumbnailCache | None = None,
        excluded: Iterable[Path] = (),
        parent=None,
    ):
        super().__init__(parent)
        self._roots = list(roots)
        self._recursive = recursive
        self._index = index
        self._progressive = progressive
        self._thumbnails = thumbnails
        self._excluded = tuple(excluded)
        self._known = {str(item.path): item for item in known or []}

    def run(self) -> None:
        with profiling.span("scan worker: scan_folders"):
            items = scan_folders(
                self._roots,
                self._recursive,
                self.isInterruptionRequested,
                self._known,
                self.partial.emit if self._progressive else None,
                # Refreshing a known library only stats files: more threads would just fight the UI for the GIL.
                SCAN_WORKERS_REFRESH if self._known else SCAN_THREADS,
                excluded=self._excluded,
            )
        if self.isInterruptionRequested():
            return
        # Every photo is the very object from the last scan (unchanged files are returned as they were):
        # nothing was added, changed or removed, so there is nothing to write back.
        unchanged = bool(self._known) and len(items) == len(self._known) and all(
            self._known.get(str(item.path)) is item for item in items
        )
        if unchanged:
            profiling.mark("scan worker: library unchanged, index and thumbnail housekeeping skipped")
        if self._index is not None and not unchanged:
            with profiling.span("scan worker: index.save"):
                self._index.save(items, self._roots, excluded=self._excluded)
        if self._thumbnails is not None and not unchanged:
            try:
                with profiling.span("scan worker: thumbnails.prune"):
                    self._thumbnails.prune(self._roots, {str(item.path) for item in items})
            except Exception:
                pass  # housekeeping only: never let it get in the way of showing the scan
        profiling.mark("scan worker done")
        self.scanned.emit(items)