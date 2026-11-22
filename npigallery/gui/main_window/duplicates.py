from PySide6.QtGui import QImage

from ...config import media_index_path
from ...filesystem import remove_file
from ...npi import build_thumbnail
from ...services import duplicates
from ...services.duplicates import DuplicateResult, DuplicateStore
from ..common.dialogs import inform, warn
from ..common.imageconv import qimage_from_bytes
from ..gallery.duplicate_worker import DuplicateWorker
from ..settings.duplicates_dialog import DuplicatesDialog


class DuplicatesMixin:
    """Settings > Storage > Duplicate photos: scanning, the review dialog and the badge on the tiles."""

    def _init_duplicates(self) -> None:
        self._dup_store = DuplicateStore(media_index_path())
        self._dup_result = DuplicateResult()
        self._dup_scanned = False
        self._dup_worker: DuplicateWorker | None = None
        self._dup_dialog: DuplicatesDialog | None = None

    def _connect_duplicates(self) -> None:
        page = self._settings_page
        page.find_duplicates_requested.connect(self._scan_duplicates)
        page.review_duplicates_requested.connect(self._review_duplicates)
        page.reset_ignored_requested.connect(self._reset_ignored_duplicates)

    def _refresh_duplicates(self) -> None:
        """Re-group from the hashes saved by the last scan (reads no photo), then update tiles and Settings."""
        try:
            result = duplicates.cached_result(self._model.library(), self._dup_store)
            # The library list may still hold photos that were just deleted (the rescan is on its way).
            self._dup_result = self._without_paths(result, {str(i.path) for g in result.groups for i in g.items if not i.path.exists()})
        except Exception:
            self._dup_result = DuplicateResult()
        self._apply_duplicates()

    @staticmethod
    def _without_paths(result: DuplicateResult, gone: set[str]) -> DuplicateResult:
        if not gone:
            return result
        groups = []
        for group in result.groups:
            items = tuple(item for item in group.items if str(item.path) not in gone)
            if len(items) > 1:
                groups.append(duplicates.DuplicateGroup(group.sha256, items))
        return DuplicateResult(tuple(groups))

    def _apply_duplicates(self) -> None:
        result = self._dup_result
        self._view.set_duplicate_paths(result.copy_paths())
        self._settings_page.set_duplicate_summary(len(result.groups), result.freeable, self._dup_scanned)
        self._settings_page.set_ignored_count(self._dup_store.ignored_count())

    # // scanning

    def _scan_duplicates(self) -> None:
        worker = self._dup_worker
        if worker is not None and worker.isRunning():
            return
        items = list(self._model.library())
        if not items:
            inform(self, "Duplicate photos", "There are no photos to scan yet.")
            return
        self._settings_page.set_duplicate_progress("Looking for photos of the same size...")
        worker = DuplicateWorker(items, media_index_path(), self)
        worker.progress.connect(self._on_duplicate_progress)
        worker.finished_scan.connect(self._on_duplicates_scanned)
        self._dup_worker = worker
        worker.start()

    def _on_duplicate_progress(self, done: int, total: int) -> None:
        self._settings_page.set_duplicate_progress(f"Comparing photos... {done:,} of {total:,}")

    def _on_duplicates_scanned(self, result) -> None:
        worker, self._dup_worker = self._dup_worker, None
        if worker is not None:
            worker.wait()
            worker.deleteLater()
        self._settings_page.set_duplicate_progress(None)
        if result is None:
            self._apply_duplicates()
            warn(self, "Duplicate photos", "The scan could not be finished.")
            return
        self._dup_scanned = True
        self._dup_result = result
        self._apply_duplicates()
        if result.groups:
            self._review_duplicates()

    def _stop_duplicates(self) -> None:
        worker = self._dup_worker
        if worker is not None:
            worker.requestInterruption()
            worker.wait(3000)
            self._dup_worker = None

    # // review

    def _review_duplicates(self) -> None:
        if self._dup_dialog is not None or not self._dup_result.groups:
            return
        dialog = DuplicatesDialog(self._dup_result, self._duplicate_thumbnail, self._delete_duplicate_copies, self)
        dialog.ignore_group_requested.connect(self._on_ignore_group)
        dialog.unignore_group_requested.connect(self._on_unignore_group)
        dialog.ignore_file_requested.connect(self._on_ignore_file)
        dialog.unignore_file_requested.connect(self._on_unignore_file)
        dialog.show_in_folder_requested.connect(self._open_item_in_explorer)
        self._dup_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._dup_dialog = None
            dialog.deleteLater()
            self._refresh_duplicates()

    def _duplicate_thumbnail(self, item) -> QImage | None:
        """Runs on a worker thread of the Duplicate photos dialog, so it returns a QImage, never a QPixmap."""
        try:
            stat = item.path.stat()
            data = self._cache.get(str(item.path), stat.st_mtime_ns, stat.st_size)
            if data is None:
                data = build_thumbnail(item.path)
                self._cache.put(str(item.path), stat.st_mtime_ns, stat.st_size, data)
            return qimage_from_bytes(data)
        except Exception:
            return None

    def _delete_duplicate_copies(self, items: list) -> set[str]:
        """Called by the dialog after it asked the person. Returns the paths that are really gone."""
        favorites = self._model.favorites()
        favorites_changed = False
        failures = []
        gone: set[str] = set()
        for item in items:
            key = str(item.path)
            try:
                remove_file(item.path)
            except OSError as error:
                failures.append(f"{item.name}: {error}")
                continue
            gone.add(key)
            if key in favorites:
                favorites.discard(key)
                favorites_changed = True
        if favorites_changed:
            self._model.set_favorites(favorites)
            self._save_favorites()
        if gone and self._albums.discard(gone.__contains__):
            self._albums_changed()
        if failures:
            warn(self._dup_dialog or self, "Remove duplicates", "\n".join(failures))
        if gone:
            self._dup_result = self._without_paths(self._dup_result, gone)
            self._apply_duplicates()
            self.reload()
        return gone

    # // not a duplicate

    def _on_ignore_group(self, sha: str) -> None:
        self._dup_store.ignore_group(sha)
        self._refresh_duplicates()

    def _on_unignore_group(self, sha: str) -> None:
        self._dup_store.unignore_group(sha)
        self._refresh_duplicates()

    def _on_ignore_file(self, path: str, sha: str) -> None:
        self._dup_store.ignore_file(path, sha)
        self._refresh_duplicates()

    def _on_unignore_file(self, path: str) -> None:
        self._dup_store.unignore_file(path)
        self._refresh_duplicates()

    def _reset_ignored_duplicates(self) -> None:
        self._dup_store.reset_ignored()
        self._refresh_duplicates()
