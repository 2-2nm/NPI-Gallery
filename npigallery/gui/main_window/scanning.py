import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt

from ... import profiling
from ...services.pruning import missing_favorites
from ..gallery.scan_worker import ScanWorker

REFRESH_COOLDOWN_SECONDS = 8.0  # coming back to the app more often than this does not rescan again
FIRST_SCAN_DELAY_MS = 1500  # a refresh of the cached library waits this long after start-up...
SCAN_IDLE_RETRY_MS = 300  # ...then until scrolling has stopped, asking again this often...
FIRST_SCAN_DEADLINE_SECONDS = 10.0  # ...but never longer than this


class ScanningMixin:
    # scanning

    _last_scan_done = 0.0
    _scan_deferred = False
    _scan_deadline = 0.0

    def _first_scan(self) -> None:
        """First scan after start-up. With the cached library already on screen it only refreshes it, so it
        waits until the user is not scrolling; with nothing cached the gallery is empty without it."""
        if not self._known:
            self._scan_deferred = False
            self.reload()
            return
        self._scan_deadline = time.monotonic() + FIRST_SCAN_DEADLINE_SECONDS
        QTimer.singleShot(FIRST_SCAN_DELAY_MS, self._scan_when_idle)

    def _scan_when_idle(self) -> None:
        if self._scan_worker is not None:  # a scan was started by something else in the meantime
            self._scan_deferred = False
            return
        if self._view.is_scrolling() and time.monotonic() < self._scan_deadline:
            QTimer.singleShot(SCAN_IDLE_RETRY_MS, self._scan_when_idle)
            return
        self._scan_deferred = False
        self._start_scan(quiet=True)

    def _show_cached(self) -> None:
        """Paint the last known library right away; the real scan refreshes it afterwards."""
        with profiling.span("index.load"):
            cached = self._index.load(self._library, self._excluded) if self._library else []
        profiling.mark(f"index returned {len(cached):,} photos")
        self._known = cached
        self._model.set_items(cached)
        self._update_header()
        self._refresh_duplicates()

    def reload(self) -> None:
        self._start_scan(quiet=False)

    def _on_application_state(self, state) -> None:
        """Back in the app (say, after deleting photos in Explorer): re-check the disk quietly."""
        if state != Qt.ApplicationState.ApplicationActive or self._scan_deferred:
            return
        worker = self._scan_worker
        if self._scanning or (worker is not None and worker.isRunning()) or not self._library:
            return
        if time.monotonic() - self._last_scan_done < REFRESH_COOLDOWN_SECONDS:
            return
        self._start_scan(quiet=True)

    def _start_scan(self, quiet: bool) -> None:
        """A quiet scan shows no "Loading..." and leaves the gallery alone unless something changed."""
        self._stop_scan()
        quiet = quiet and bool(self._known) and bool(self._library)
        self._scanning = not quiet
        if not quiet:
            self._update_header()
        if not self._library:
            self._scanning = False
            self._known = []
            self._model.set_items([])
            self._update_header()
            return
        profiling.mark(f"scan starts (known={len(self._known):,}, quiet={quiet}, progressive={not self._known})")
        worker = ScanWorker(
            list(self._library),
            True,  # subfolders are always included
            self._index,
            self._known,
            progressive=not self._known,
            thumbnails=self._cache,
            excluded=list(self._excluded),
        )
        worker.scanned.connect(self._on_scanned)
        worker.partial.connect(self._on_partial)
        self._scan_worker = worker
        worker.start()

    def _stop_scan(self) -> None:
        worker = self._scan_worker
        if worker is None:
            return
        for signal, slot in ((worker.scanned, self._on_scanned), (worker.partial, self._on_partial)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        worker.requestInterruption()
        worker.wait()
        self._scan_worker = None

    def _on_partial(self, items: list) -> None:
        if self._scanning:
            with profiling.span(f"partial scan snapshot applied ({len(items):,} photos)"):
                self._model.set_items(items, keep_scroll=True)
            self._update_header()

    def _on_scanned(self, items: list) -> None:
        self._scanning = False
        self._last_scan_done = time.monotonic()
        before = [(item.path, item.mtime_ns, item.file_size) for item in self._known]
        after = [(item.path, item.mtime_ns, item.file_size) for item in items]
        had_partials = not self._known
        self._known = items
        profiling.mark(f"scan finished in the UI thread: {len(items):,} photos, changed={before != after}")
        if before != after or had_partials:
            with profiling.span("final scan result applied"):
                self._model.set_items(items, keep_scroll=True)
            self._refresh_duplicates()
        self._update_header()
        self._prune_missing_favorites(items)
        self._prune_missing_album_files(items)

    def _prune_missing_favorites(self, items: list) -> None:
        favorites = self._model.favorites()
        if not favorites:
            return
        # Keep favourites inside hidden folders intact so they reappear on Restore.
        visible_favorites = {
            fav
            for fav in favorites
            if not any(Path(fav).is_relative_to(ex) for ex in self._excluded)
        }
        gone = missing_favorites(visible_favorites, {str(item.path) for item in items}, self._library)
        if gone:
            self._model.set_favorites(favorites - gone)
            self._save_favorites()

    def _prune_missing_album_files(self, items: list) -> None:
        # Same rules as favourites: files inside hidden folders or on a drive that is not there stay listed.
        keys = self._albums.all_paths()
        if not keys:
            return
        visible = {key for key in keys if not any(Path(key).is_relative_to(ex) for ex in self._excluded)}
        gone = missing_favorites(visible, {str(item.path) for item in items}, self._library)
        if gone and self._albums.discard(gone.__contains__):
            self._albums_changed()
