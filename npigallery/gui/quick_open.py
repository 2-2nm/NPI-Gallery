import getpass
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..config import APP_NAME, media_index_path, thumbnail_database_path
from ..filesystem import remove_file
from ..npi import is_video_path
from ..services import explorer_order
from ..services.index import MediaIndex
from ..services.rotate import RotateError, rotate_file
from ..services.scanner import MediaItem, describe_media, is_media_path, scan_folder
from ..services.thumbcache import ThumbnailCache
from ..services.wallpaper import WallpaperError, set_desktop_wallpaper
from ..services.wallpaper import is_supported as wallpaper_supported
from .common.dialogs import confirm, warn
from .gallery.thumbnails import ThumbnailLoader
from .theme.preferences import prefs
from .viewer import ViewerHooks, ViewerWindow, video_playback_available

HANDOVER_TIMEOUT_MS = 400
SAME_BATCH_SECONDS = 1.5
ASFW_ANY = 0xFFFFFFFF
INVALID_NAME_CHARACTERS = '\\/:*?"<>|'


def media_arguments(argv: list[str]) -> list[Path]:
    paths = []
    for value in argv[1:]:
        path = Path(value)
        if path.is_file() and is_media_path(path):
            paths.append(path.resolve())
    return paths


def _server_name() -> str:
    user = re.sub(r"[^A-Za-z0-9_]", "_", getpass.getuser())
    return f"{APP_NAME}-{user}-open"


def hand_over(paths: list[Path]) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            ctypes.windll.user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
            ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
        except (OSError, AttributeError):
            pass
    socket = QLocalSocket()
    socket.connectToServer(_server_name())
    if not socket.waitForConnected(HANDOVER_TIMEOUT_MS):
        return False
    socket.write(json.dumps([str(path) for path in paths]).encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(HANDOVER_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


class Receiver(QObject):
    received = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self.listening = self._server.listen(_server_name())

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            buffer = bytearray()
            socket.readyRead.connect(lambda socket=socket, buffer=buffer: buffer.extend(socket.readAll().data()))
            socket.disconnected.connect(lambda socket=socket, buffer=buffer: self._finish(socket, bytes(buffer)))

    def _finish(self, socket: QLocalSocket, data: bytes) -> None:
        socket.deleteLater()
        try:
            values = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        paths = [Path(value) for value in values if isinstance(value, str)]
        paths = [path for path in paths if path.is_file() and is_media_path(path)]
        if paths:
            self.received.emit(paths)


class QuickOpen(QObject):
    thumbnails_changed = Signal()  # the viewer repaints its filmstrip when this fires

    def __init__(self, parent=None):
        super().__init__(parent)
        self._viewers: list[tuple[ViewerWindow, str, float]] = []
        self._thumbnails: ThumbnailCache | None = None
        self._loader: ThumbnailLoader | None = None
        self._pixmaps: dict[str, QPixmap] = {}
        self._failed: set[str] = set()
        if parent is not None and hasattr(parent, "aboutToQuit"):
            parent.aboutToQuit.connect(self._shutdown)

    def open_paths(self, paths: list[Path]) -> None:
        for path in paths:
            self.open_path(path)

    def open_path(self, path: Path) -> None:
        folder_key = os.path.normcase(str(path.parent))
        now = time.monotonic()
        for viewer, key, opened_at in self._viewers:
            if key != folder_key:
                continue
            if now - opened_at > SAME_BATCH_SECONDS:
                viewer.show_path(path)
            self._raise(viewer)
            return
        if is_video_path(path) and not video_playback_available():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        built = self._build_items(path)
        if built is None:
            warn(None, "Open", f"“{path.name}” can't be opened.")
            return
        items, row = built
        viewer = ViewerWindow(items, row, None, hooks=self._hooks())
        self._viewers.append((viewer, folder_key, now))
        viewer.closed.connect(lambda viewer=viewer: self._forget(viewer))
        viewer.show()
        self._raise(viewer)

    def has_viewers(self) -> bool:
        return bool(self._viewers)

    def _forget(self, viewer: ViewerWindow) -> None:
        self._viewers = [entry for entry in self._viewers if entry[0] is not viewer]

    @staticmethod
    def _raise(viewer: ViewerWindow) -> None:
        if viewer.isMinimized():
            viewer.showNormal()
        viewer.raise_()
        viewer.activateWindow()

    def _build_items(self, path: Path) -> tuple[list[MediaItem], int] | None:
        clicked = describe_media(path)
        if clicked is None:
            return None
        known: dict[str, MediaItem] = {}
        try:
            known = {str(item.path): item for item in MediaIndex(media_index_path()).load([path.parent])}
        except OSError:
            pass
        items = scan_folder(path.parent, recursive=False, known=known)
        items = self._in_explorer_order(items, path)
        target = os.path.normcase(str(path))
        row = next((index for index, item in enumerate(items) if os.path.normcase(str(item.path)) == target), None)
        if row is None:
            items.insert(0, clicked)
            row = 0
        return items, row

    @staticmethod
    def _in_explorer_order(items: list[MediaItem], clicked: Path) -> list[MediaItem]:
        """Neighbours as the file manager shows them, when an Explorer window is open on this folder."""
        columns = explorer_order.view_sort(clicked.parent, clicked)
        if not columns:
            return items
        return explorer_order.sort_like_explorer(items, columns) or items

    def _hooks(self) -> ViewerHooks:
        return ViewerHooks(
            delete=self._delete,
            rename=self._rename,
            rotate=self._rotate,
            set_wallpaper=self._set_wallpaper if wallpaper_supported() else None,
            thumbnail_for=self._thumbnail,
            thumbnails_changed=self.thumbnails_changed,
        )

    def _cache(self) -> ThumbnailCache:
        if self._thumbnails is None:
            self._thumbnails = ThumbnailCache(thumbnail_database_path())
        return self._thumbnails

    def _thumbnail(self, item: MediaItem) -> QPixmap | None:
        key = str(item.path)
        pixmap = self._pixmaps.get(key)
        if pixmap is not None:
            return pixmap
        if key in self._failed:
            return None
        data = self._cache().get(key, item.mtime_ns, item.file_size)
        if data:
            loaded = QPixmap()
            if loaded.loadFromData(data):
                self._pixmaps[key] = loaded
                return loaded
        # Not in the cache (a folder the gallery never scanned): build it in the background and
        # repaint the filmstrip when it arrives, instead of leaving an empty box forever.
        if self._loader is None:
            self._loader = ThumbnailLoader(self._cache(), self)
            self._loader.loaded.connect(self._on_thumbnail_loaded)
            self._loader.failed.connect(self._on_thumbnail_failed)
        self._loader.enqueue(item)
        return None

    def _on_thumbnail_loaded(self, key: str, image: QImage) -> None:
        self._pixmaps[key] = QPixmap.fromImage(image)
        self.thumbnails_changed.emit()

    def _on_thumbnail_failed(self, key: str) -> None:
        self._failed.add(key)

    def _shutdown(self) -> None:
        if self._loader is not None:
            self._loader.shutdown()
        if self._thumbnails is not None:
            self._thumbnails.flush()

    def _delete(self, item: MediaItem, parent) -> bool:
        if prefs().get("confirm_delete") and not confirm(parent, "Delete", "Delete 1 item?", ok_text="Delete", danger=True):
            return False
        try:
            remove_file(item.path)
        except OSError as error:
            warn(parent, "Delete", f"{item.name}: {error}")
            return False
        return True

    def _rename(self, item: MediaItem, name: str, parent) -> MediaItem | None:
        suffix = item.path.suffix
        cleaned = name.strip().rstrip(". ")
        if not cleaned or any(char in INVALID_NAME_CHARACTERS or ord(char) < 32 for char in cleaned):
            warn(parent, "Rename", f"That name is not valid. Avoid {' '.join(INVALID_NAME_CHARACTERS)}")
            return None
        new_name = cleaned if cleaned.casefold().endswith(suffix.casefold()) else f"{cleaned}{suffix}"
        if new_name == item.path.name:
            return None
        target = item.path.with_name(new_name)
        if target.exists() and str(target).casefold() != str(item.path).casefold():
            warn(parent, "Rename", f"“{new_name}” already exists.")
            return None
        try:
            item.path.rename(target)
        except OSError as error:
            warn(parent, "Rename", str(error))
            return None
        self._pixmaps.pop(str(item.path), None)
        self._failed.discard(str(item.path))
        return replace(item, path=target, name=target.name)

    def _rotate(self, item: MediaItem, degrees: int, parent) -> MediaItem | None:
        try:
            rotate_file(item.path, degrees)
        except RotateError as error:
            warn(parent, "Rotate", str(error))
            return None
        self._pixmaps.pop(str(item.path), None)
        self._failed.discard(str(item.path))
        return describe_media(item.path) or replace(item, width=item.height, height=item.width)

    def _set_wallpaper(self, item: MediaItem, parent) -> None:
        try:
            set_desktop_wallpaper(item.path)
        except WallpaperError as error:
            warn(parent, "Set as wallpaper", str(error))