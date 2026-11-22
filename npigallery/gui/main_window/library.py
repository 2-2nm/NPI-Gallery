import json
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from ...filesystem import moves_to_trash, remove_folder, unique_path
from ..common.dialogs import ask_text, confirm, warn
from ..common.scope import Scope


class LibraryMixin:
    # library, excluded subfolders and favourites

    def _load_library(self, start_folder: Path | None) -> list[Path]:
        folders: list[Path] = []
        try:
            stored = json.loads(self._settings.value("library_json", "[]", str))
        except (TypeError, ValueError):
            stored = []
        for value in stored:
            if isinstance(value, str) and Path(value).is_dir():
                folders.append(Path(value))
        legacy = self._settings.value("last_folder", "", str)
        if not folders and legacy and Path(legacy).is_dir():
            folders.append(Path(legacy))
        if not folders:
            location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
            if location and Path(location).is_dir():
                folders.append(Path(location))
        if start_folder is not None and start_folder not in folders:
            folders.append(start_folder)
        return folders

    def _save_library(self) -> None:
        self._settings.setValue("library_json", json.dumps([str(folder) for folder in self._library]))

    def _load_excluded(self) -> list[Path]:
        folders: list[Path] = []
        try:
            stored = json.loads(self._settings.value("excluded_json", "[]", str))
        except (TypeError, ValueError):
            stored = []
        for value in stored:
            if not isinstance(value, str):
                continue
            path = Path(value)
            if path.is_dir() and path not in folders and any(path != root and path.is_relative_to(root) for root in self._library):
                folders.append(path)
        folders.sort(key=lambda path: (path.name.casefold(), str(path).casefold()))
        return folders

    def _visible_excluded(self) -> list[Path]:
        return [
            path
            for path in self._excluded
            if any(path != root and path.is_relative_to(root) for root in self._library)
        ]

    def _save_excluded(self) -> None:
        self._excluded = self._visible_excluded()
        self._excluded.sort(key=lambda path: (path.name.casefold(), str(path).casefold()))
        self._settings.setValue("excluded_json", json.dumps([str(folder) for folder in self._excluded]))
        if hasattr(self, "_settings_page"):
            self._settings_page.set_hidden_folders(self._excluded)

    def _load_favorites(self) -> set[str]:
        try:
            return {
                value
                for value in json.loads(self._settings.value("favorites_json", "[]", str))
                if isinstance(value, str)
            }
        except (TypeError, ValueError):
            return set()

    def _save_favorites(self) -> None:
        self._settings.setValue("favorites_json", json.dumps(sorted(self._model.favorites())))
        self._update_header()

    def _add_folder(self) -> None:
        start = str(self._library[-1]) if self._library else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Add folder to gallery", start)
        if not chosen:
            return
        folder = Path(chosen)
        remaining = [ex for ex in self._excluded if not (folder == ex or folder.is_relative_to(ex))]
        unexcluded = len(remaining) != len(self._excluded)
        if unexcluded:
            self._excluded = remaining
            self._save_excluded()
        covered_by_root = any(folder == root or folder.is_relative_to(root) for root in self._library)
        added_root = False
        if not covered_by_root and folder not in self._library:
            self._library.append(folder)
            self._save_library()
            added_root = True
        if added_root or unexcluded:
            self._sidebar.tree.set_library(self._library, self._excluded)
        self._sidebar.tree.select_scope(Scope.folder(folder))
        self.reload()

    def _remove_folder(self, folder: Path) -> None:
        if folder in self._library:
            self._library.remove(folder)
            self._save_library()
            # Clean up any excluded subfolders that belonged only to this removed root.
            self._save_excluded()
            self._sidebar.tree.set_library(self._library, self._excluded)
            self.reload()
            return
        if not any(folder.is_relative_to(root) for root in self._library):
            return
        if not any(folder == ex or folder.is_relative_to(ex) for ex in self._excluded):
            self._excluded = [ex for ex in self._excluded if not ex.is_relative_to(folder)]
            self._excluded.append(folder)
            self._save_excluded()
        self._known = [
            item for item in self._known if not (item.path.parent == folder or item.path.is_relative_to(folder))
        ]
        self._model.set_items(self._known, keep_scroll=True)
        self._sidebar.tree.set_library(self._library, self._excluded)
        self.reload()

    def _restore_folder(self, folder: Path) -> None:
        if folder not in self._excluded:
            return
        self._excluded = [ex for ex in self._excluded if ex != folder]
        self._save_excluded()
        self._sidebar.tree.set_library(self._library, self._excluded)
        self.reload()

    # folder context menu

    def _on_folder_action(self, action: str, folder: Path) -> None:
        handler = {
            "create": self._create_folder,
            "rename": self._rename_folder,
            "slideshow": self._slideshow_folder,
            "explorer": self._open_in_explorer,
            "delete": self._delete_folder,
        }.get(action)
        if handler is not None:
            handler(folder)

    def _ask_folder_name(self, title: str, text: str) -> str | None:
        name = ask_text(self, title, "Folder name", text)
        if name is None:
            return None
        name = name.strip().rstrip(". ")
        if not name or any(char in '\\/:*?"<>|' or ord(char) < 32 for char in name):
            warn(self, title, 'That name is not valid. Avoid \\ / : * ? " < > |')
            return None
        return name

    def _create_folder(self, parent: Path) -> None:
        name = self._ask_folder_name("Create folder", unique_path(parent / "New folder").name)
        if name is None:
            return
        target = parent / name
        try:
            target.mkdir()
        except FileExistsError:
            warn(self, "Create folder", f"“{name}” already exists.")
            return
        except OSError as error:
            warn(self, "Create folder", str(error))
            return
        self._sidebar.tree.add_created_folder(parent, target)

    def _rename_folder(self, folder: Path) -> None:
        name = self._ask_folder_name("Rename", folder.name)
        if name is None or name == folder.name:
            return
        target = folder.with_name(name)
        if target.exists() and str(target).casefold() != str(folder).casefold():
            warn(self, "Rename", f"“{name}” already exists.")
            return
        try:
            folder.rename(target)
        except OSError as error:
            warn(self, "Rename", str(error))
            return

        def moved(path: Path) -> Path:
            return target / path.relative_to(folder) if path == folder or path.is_relative_to(folder) else path

        self._library = [moved(path) for path in self._library]
        self._save_library()
        excluded = [moved(path) for path in self._excluded]
        if excluded != self._excluded:
            self._excluded = excluded
            self._save_excluded()
        favorites = {str(moved(Path(value))) for value in self._model.favorites()}
        if favorites != self._model.favorites():
            self._model.set_favorites(favorites)
            self._save_favorites()
        if self._albums.remap(lambda key: str(moved(Path(key)))):
            self._albums_changed()
        self._known = [
            replace(item, path=moved(item.path)) if item.path.is_relative_to(folder) else item
            for item in self._known
        ]
        self._model.set_items(self._known, keep_scroll=True)
        self._sidebar.tree.rename_folder(folder, target)
        self._on_scope_selected(self._sidebar.tree.current_scope())
        self.reload()

    def _open_in_explorer(self, folder: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _delete_folder(self, folder: Path) -> None:
        detail = "It will be moved to the Recycle Bin." if moves_to_trash() else "This cannot be undone."
        if not confirm(
            self,
            "Delete folder",
            f"Delete “{folder.name or folder}” and everything inside it?\n\n{detail}",
            ok_text="Delete",
            danger=True,
        ):
            return
        try:
            remove_folder(folder)
        except OSError as error:
            warn(self, "Delete folder", str(error))
            return

        def inside(path: Path) -> bool:
            return path == folder or path.is_relative_to(folder)

        library = [path for path in self._library if not inside(path)]
        library_changed = len(library) != len(self._library)
        if library_changed:
            self._library = library
            self._save_library()
        excluded = [path for path in self._excluded if not inside(path)]
        if len(excluded) != len(self._excluded) or library_changed:
            self._excluded = excluded
            self._save_excluded()
        favorites = {value for value in self._model.favorites() if not inside(Path(value))}
        if favorites != self._model.favorites():
            self._model.set_favorites(favorites)
            self._save_favorites()
        if self._albums.discard(lambda key: inside(Path(key))):
            self._albums_changed()
        self._known = [item for item in self._known if not inside(item.path)]
        self._model.set_items(self._known, keep_scroll=True)
        if library_changed:
            self._sidebar.tree.set_library(self._library, self._excluded)
        else:
            self._sidebar.tree.remove_folder(folder)
        self.reload()