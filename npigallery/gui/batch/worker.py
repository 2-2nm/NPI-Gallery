from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...services.batch import BatchResult


class BatchWorker(QThread):
    progress = Signal(int, int)
    stats = Signal(int, int, int)  # processed, skipped, failed
    completed = Signal(object)

    def __init__(self, job: Callable[..., BatchResult], parent=None):
        super().__init__(parent)
        self._job = job

    def run(self) -> None:
        try:
            result = self._job(
                on_progress=self.progress.emit,
                on_stats=lambda r: self.stats.emit(r.processed, r.skipped, len(r.failures)),
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as error:
            result = BatchResult(failures=[(Path("."), str(error))])
        self.completed.emit(result)
