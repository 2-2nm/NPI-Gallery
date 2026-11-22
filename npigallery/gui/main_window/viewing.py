import gc
import os
import weakref
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from ...services.wallpaper import is_supported as wallpaper_supported
from ..common.dialogs import inform
from ..viewer import ViewerHooks, ViewerWindow, video_playback_available
from ..viewer.classic import ViewerWindow as ClassicViewerWindow


class ViewingMixin:
    def _slideshow_folder(self, folder: Path) -> None:
        items = self._model.items_under(folder)
        if not items:
            inform(self, "Slideshow", "There are no photos in this folder.")
            return
        self._launch_viewer(items, 0, slideshow=True)

    def _launch_viewer(self, items: list, row: int, slideshow: bool = False) -> None:
        """Open a photo in its own top-level window (own taskbar button, minimize/maximize).
        Set NPI_CLASSIC_VIEWER=1 to get the old side-panel viewer instead."""
        # The viewer plays videos itself. Without QtMultimedia, or in the classic side-panel viewer
        # (photos only), a video opens in the system player and the viewer is handed the photos alone
        # (the row is translated to the same photo in the shorter list).
        classic = bool(os.environ.get("NPI_CLASSIC_VIEWER"))
        if classic or not video_playback_available():
            if 0 <= row < len(items) and items[row].is_video and not slideshow:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(items[row].path)))
                return
            photos = [item for item in items if not item.is_video]
            if len(photos) != len(items):
                row = sum(1 for item in items[:max(row, 0)] if not item.is_video)
                items = photos
                if not items:
                    inform(self, "Slideshow" if slideshow else "Open", "There are no photos here.")
                    return
                row = min(row, len(items) - 1)
        if classic:
            viewer = ClassicViewerWindow(items, row, self, slideshow=slideshow)
            viewer.show()
            return
        model = self._model
        hooks = ViewerHooks(
            is_favorite=model.is_favorite,
            toggle_favorite=self._toggle_favorite_item,
            delete=lambda item, parent: self._delete_items([item], parent),
            edit=lambda item: self._edit_items([item]),
            rename=self._rename_to,
            rotate=self._rotate_item,
            save_edit=self._save_edit,
            set_sidebar_background=self._set_sidebar_background,
            set_wallpaper=self._set_wallpaper if wallpaper_supported() else None,
            thumbnail_for=model.pixmap_for,
            thumbnails_changed=model.dataChanged,
        )
        viewer = ViewerWindow(items, row, None, slideshow=slideshow, hooks=hooks)
        self._viewers.append(viewer)
        ref = weakref.ref(viewer)  # a strong reference here would keep the closed window (and its photos) alive
        viewer.closed.connect(lambda: self._viewer_closed(ref))
        viewer.show()
        viewer.activateWindow()

    def _viewer_closed(self, ref) -> None:
        viewer = ref()
        if viewer is not None and viewer in self._viewers:
            self._viewers.remove(viewer)
        del viewer
        # The closed window is deleted on the next event-loop turn; collect what it leaves behind after that.
        QTimer.singleShot(400, gc.collect)

    def _open_viewer(self, row: int) -> None:
        if row < 0 or row >= self._model.rowCount():
            return
        self._launch_viewer(self._model.items(), row)

    def _start_slideshow(self) -> None:
        count = self._model.rowCount()
        if not count:
            return
        selected = self._view.selected_rows()
        self._launch_viewer(self._model.items(), selected[0] if selected else 0, slideshow=True)

    # context menu & shortcut actions

    def _start_slideshow_at(self, row: int) -> None:
        if row < 0 or row >= self._model.rowCount():
            return
        self._launch_viewer(self._model.items(), row, slideshow=True)

    def _slideshow_active(self) -> None:
        row = self._active_row()
        if row is not None:
            self._start_slideshow_at(row)
        else:
            self._start_slideshow()