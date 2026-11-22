import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from ...config import cache_directory
from ...filesystem import remove_file, unique_path, write_atomic
from ...npi import read_image_bytes, read_meta
from ...services.editing import save_edit
from ...services.rotate import RotateError, rotate_file
from ...services.scanner import describe_media
from ...services.wallpaper import WallpaperError, set_desktop_wallpaper
from ...services.wallpaper import is_supported as wallpaper_supported
from ..common.dialogs import ask_text, confirm, warn
from ..common.imageconv import qimage_from_bytes
from ..common.scope import Scope
from ..theme.preferences import prefs
from .frameless import IS_WINDOWS
from .share_dialog import ShareDialog


class ItemActionsMixin:
    def _toggle_favorite_item(self, item) -> None:
        row = self._model.row_for_path(item.path)
        if row is not None:
            self._model.toggle_favorite(row)

    def _edit_active(self) -> None:
        items = self._active_items()
        if items:
            self._edit_items(items)

    def _edit_items(self, items: list) -> None:
        if not items:
            return
        item = items[0]
        try:
            if item.is_npi:
                meta = read_meta(item.path)
                data = read_image_bytes(item.path)
                temp_dir = cache_directory() / "edit-preview"
                temp_dir.mkdir(parents=True, exist_ok=True)
                target = temp_dir / meta.original_name
                write_atomic(target, data)
            else:
                target = item.path
        except Exception as error:
            warn(self, "Edit", str(error))
            return

        if IS_WINDOWS:
            try:
                os.startfile(str(target), "edit")
                return
            except OSError:
                try:
                    subprocess.Popen(["mspaint.exe", str(target)])
                    return
                except OSError:
                    os.startfile(str(target))
                    return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Preview", str(target)])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _set_sidebar_background(self, item) -> None:
        """Photo menu -> "Set as sidebar background": open the same crop dialog as Settings."""
        self._set_picture_background(
            item, "sidebar-picture", "sidebar_bg_path", self._settings_page.background_card
        )

    def _set_window_background(self, item) -> None:
        """Photo menu -> "Set as background": the picture behind the whole window, framed in the crop dialog."""
        self._set_picture_background(
            item, "window-picture", "window_bg_path", self._settings_page.window_background_card
        )

    def _set_picture_background(self, item, folder_name: str, path_key: str, card) -> None:
        try:
            if item.is_npi:
                # Pillow can't read a .npi container, so the image inside goes to a plain file first.
                meta = read_meta(item.path)
                folder = cache_directory() / folder_name
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{meta.sha256[:12]}{Path(meta.original_name).suffix.lower()}"
                if not target.is_file():
                    write_atomic(target, read_image_bytes(item.path))
                path = target
            else:
                path = item.path
        except Exception as error:
            warn(self, card.TITLE, str(error))
            return
        # The photo viewer is its own window: bring the gallery (where the dialog opens) in front of it.
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        card.apply_picture(str(path))
        self._prune_decoded_pictures(folder_name, path_key)

    def _wallpaper_active(self) -> None:
        """Ctrl+B: the selected (or current) photo becomes the desktop wallpaper."""
        if not wallpaper_supported() or self._search.hasFocus():
            return
        items = self._active_items()
        if len(items) == 1 and not items[0].is_video:
            self._set_wallpaper(items[0])

    def _set_wallpaper(self, item, parent: QWidget | None = None) -> None:
        """Photo menu / viewer toolbar -> "Set as wallpaper": the desktop wallpaper, no dialog."""
        try:
            set_desktop_wallpaper(item.path)
        except WallpaperError as error:
            warn(parent or self, "Set as wallpaper", str(error))

    @staticmethod
    def _prune_decoded_pictures(folder_name: str, path_key: str) -> None:
        """Keep only the decoded .npi picture that the background in `path_key` currently uses."""
        keep = prefs().get(path_key)
        for file in (cache_directory() / folder_name).glob("*"):
            if str(file) != keep:
                file.unlink(missing_ok=True)

    def _copy_selected(self) -> None:
        self._copy_items(self._selected_items())

    def _copy_active(self) -> None:
        if self._search.hasFocus():
            self._search.copy()
            return
        self._copy_items(self._active_items())

    def _copy_items(self, items: list) -> None:
        if not items:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(item.path)) for item in items])
        try:
            image = qimage_from_bytes(read_image_bytes(items[0].path))
            if not image.isNull():
                mime.setImageData(image)
        except Exception:
            pass
        QApplication.clipboard().setMimeData(mime)

    def _copy_path_active(self) -> None:
        self._copy_paths(self._active_items())

    def _copy_paths(self, items: list) -> None:
        if not items:
            return
        formatted = "\n".join(f'"{item.path}"' for item in items)
        QApplication.clipboard().setText(formatted)

    def _share_active(self) -> None:
        self._share_items(self._active_items())

    def _share_items(self, items: list) -> None:
        if not items:
            return
        dialog = ShareDialog(
            items,
            copy_files_fn=self._copy_items,
            copy_paths_fn=self._copy_paths,
            reveal_fn=self._open_item_in_explorer,
            parent=self,
        )
        dialog.exec()
        dialog.deleteLater()

    def _rename_active(self) -> None:
        items = self._active_items()
        if len(items) == 1:
            self._rename_item(items[0])

    def _rename_item(self, item) -> None:
        name = ask_text(self, "Rename", "File name", item.path.stem)
        if name is None:
            return
        self._rename_to(item, name, self)

    def _rename_to(self, item, name: str, parent):
        """Rename `item` to `name` (the extension is kept). Returns the renamed item, or None when
        nothing was renamed; problems are reported on `parent`. Also used by the photo viewer."""
        suffix = item.path.suffix
        cleaned = name.strip().rstrip(". ")
        if not cleaned or any(char in '\\/:*?"<>|' or ord(char) < 32 for char in cleaned):
            warn(parent, "Rename", 'That name is not valid. Avoid \\ / : * ? " < > |')
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

        old_key = str(item.path)
        new_key = str(target)
        favorites = self._model.favorites()
        if old_key in favorites:
            favorites.discard(old_key)
            favorites.add(new_key)
            self._model.set_favorites(favorites)
            self._save_favorites()
        if self._albums.remap(lambda key: new_key if key == old_key else key):
            self._albums_changed()
        self._known = [
            replace(entry, path=target, name=target.name) if entry.path == item.path else entry
            for entry in self._known
        ]
        self._model.set_items(self._known, keep_scroll=True)
        row = self._model.row_for_path(target)
        if row is not None:
            self._view.focus_row(row)
        self.reload()
        return replace(item, path=target, name=target.name)

    def _rotate_item(self, item, degrees: int, parent):
        """Rotate the photo's file clockwise by `degrees`. Returns the item as it is now, or None
        when it could not be rotated (the reason is shown on `parent`). Used by the photo viewer."""
        try:
            rotate_file(item.path, degrees)
        except RotateError as error:
            warn(parent, "Rotate", str(error))
            return None
        fresh = describe_media(item.path) or replace(item, width=item.height, height=item.width)
        self._known = [fresh if entry.path == item.path else entry for entry in self._known]
        self._model.set_items(self._known, keep_scroll=True)
        self._model.forget_pixmap(item.path)
        row = self._model.row_for_path(item.path)
        if row is not None:
            self._view.focus_row(row)
        return fresh

    def _save_edit(self, item, state, copy: bool, parent):
        """Write an edit made in the in-app editor: next to the photo as a copy, or over it.

        Returns the item as it is now (the new item for a copy), or None when it could not be saved
        (the reason is shown on `parent`). Used by the photo viewer."""
        try:
            target = save_edit(item.path, state, copy)
        except RotateError as error:
            warn(parent, "Edit", str(error))
            return None
        fresh = describe_media(target)
        if fresh is None:
            warn(parent, "Edit", "The edited photo was saved but could not be read back.")
            return None
        if copy:
            self._known = [*self._known, fresh]
        else:
            self._known = [fresh if entry.path == item.path else entry for entry in self._known]
            self._model.forget_pixmap(item.path)
        self._model.set_items(self._known, keep_scroll=True)
        row = self._model.row_for_path(target)
        if row is not None:
            self._view.focus_row(row)
        return fresh

    def _explorer_active(self) -> None:
        items = self._active_items()
        if items:
            self._open_item_in_explorer(items[0])

    def _open_item_in_explorer(self, item) -> None:
        path = item.path
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(str(path))])
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _go_to_item_folder(self, item) -> None:
        folder = item.path.parent
        in_library = any(folder == root or folder.is_relative_to(root) for root in self._library)
        if not in_library and folder.is_dir():
            self._library.append(folder)
            self._save_library()
            self._sidebar.tree.set_library(self._library)
        self._sidebar.tree.select_scope(Scope.folder(folder))
        row = self._model.row_for_path(item.path)
        if row is not None:
            self._view.focus_row(row)

    def _delete_selected(self) -> None:
        self._delete_items(self._selected_items())

    def _delete_active(self) -> None:
        if self._search.hasFocus():
            return
        self._delete_items(self._active_items())

    def _delete_items(self, items: list, dialog_parent: QWidget | None = None) -> bool:
        """Delete `items` (asking first when the preference says so). True if anything was removed."""
        if not items:
            return False
        count = len(items)
        owner = dialog_parent or self
        if prefs().get("confirm_delete"):
            if not confirm(
                owner,
                "Delete",
                f"Delete {count} item{'s' if count != 1 else ''}?",
                ok_text="Delete",
                danger=True,
            ):
                return False
        favorites = self._model.favorites()
        favorites_changed = False
        failures = []
        removed = 0
        removed_keys: set[str] = set()
        for item in items:
            try:
                remove_file(item.path)
                removed += 1
                key = str(item.path)
                removed_keys.add(key)
                if key in favorites:
                    favorites.discard(key)
                    favorites_changed = True
            except OSError as error:
                failures.append(f"{item.name}: {error}")
        if favorites_changed:
            self._model.set_favorites(favorites)
            self._save_favorites()
        if removed_keys and self._albums.discard(removed_keys.__contains__):
            self._albums_changed()
        if failures:
            warn(owner, "Delete", "\n".join(failures))
        self._view.clear_selection()
        self.reload()
        return removed > 0

    def _move_or_copy_selected(self, move: bool) -> None:
        self._move_or_copy_items(self._selected_items(), move=move)

    def _move_or_copy_items(self, items: list, move: bool) -> None:
        if not items:
            return
        start = str(items[0].path.parent)
        title = "Move to folder" if move else "Copy to folder"
        chosen = QFileDialog.getExistingDirectory(self, title, start)
        if not chosen:
            return
        target_dir = Path(chosen)
        favorites = self._model.favorites()
        favorites_changed = False
        failures = []
        moved_keys: dict[str, str] = {}
        for item in items:
            if move and item.path.parent == target_dir:
                continue
            destination = unique_path(target_dir / item.path.name)
            try:
                if move:
                    shutil.move(str(item.path), str(destination))
                    old_key = str(item.path)
                    moved_keys[old_key] = str(destination)
                    if old_key in favorites:
                        favorites.discard(old_key)
                        favorites.add(str(destination))
                        favorites_changed = True
                else:
                    shutil.copy2(str(item.path), str(destination))
            except OSError as error:
                failures.append(f"{item.name}: {error}")
        if favorites_changed:
            self._model.set_favorites(favorites)
            self._save_favorites()
        if moved_keys and self._albums.remap(lambda key: moved_keys.get(key, key)):
            self._albums_changed()
        if failures:
            warn(self, title, "\n".join(failures))
        self._view.clear_selection()
        self.reload()