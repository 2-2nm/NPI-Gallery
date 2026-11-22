from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from ...services.scanner import media_subfolders


class ChildrenSignals(QObject):
    ready = Signal(int, str, object)  # generation, folder path, list[Path]


class ChildrenJob(QRunnable):
    """Finds, off the UI thread, which subfolders of a folder contain photos."""

    def __init__(
        self,
        generation: int,
        folder: Path,
        cache: dict[str, bool],
        signals: ChildrenSignals,
        pause=None,
        excluded: Iterable[Path] = (),
    ):
        super().__init__()
        self._pause = pause
        self._generation = generation
        self._folder = folder
        self._cache = cache
        self._signals = signals
        self._excluded = tuple(excluded)

    def run(self) -> None:
        folders = media_subfolders(self._folder, self._cache, self._pause, excluded=self._excluded)
        try:
            self._signals.ready.emit(self._generation, str(self._folder), folders)
        except RuntimeError:
            pass  # the tree was destroyed while scanning