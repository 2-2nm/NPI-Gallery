from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...services import duplicates
from ...services.duplicates import DuplicateResult, DuplicateStore
from ...services.scanner import MediaItem


class DuplicateWorker(QThread):
    """Reads the photos that could be copies of each other and groups the ones that are."""

    progress = Signal(int, int)  # hashed so far, to hash
    finished_scan = Signal(object)  # DuplicateResult, or None when it failed

    def __init__(self, items: list[MediaItem], database: Path, parent=None):
        super().__init__(parent)
        self._items = list(items)
        self._database = database

    def run(self) -> None:
        try:
            store = DuplicateStore(self._database)
            result: DuplicateResult | None = duplicates.scan(
                self._items, store, self.isInterruptionRequested, self.progress.emit
            )
        except Exception:
            result = None
        if self.isInterruptionRequested():
            return
        self.finished_scan.emit(result)
