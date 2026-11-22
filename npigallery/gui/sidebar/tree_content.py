import os
import time
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

from ...services.scanner import media_subfolders
from ..common.scope import Scope
from .children_loader import ChildrenJob
from .metrics import KIND_ROLE, PATH_ROLE, ROOT_ROLE, SCOPE_KINDS, SCOPE_ROLE


class NavContentMixin:
    def _add(
        self,
        parent,
        text: str,
        kind: str,
        path: Path | str | None = None,
        selectable: bool = True,
        root: bool = False,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent) if parent is not None else QTreeWidgetItem(self)
        item.setText(0, text)
        item.setData(0, KIND_ROLE, kind)
        if path is not None:
            item.setData(0, PATH_ROLE, str(path))
        item.setData(0, ROOT_ROLE, root)
        flags = Qt.ItemFlag.ItemIsEnabled
        if selectable:
            flags |= Qt.ItemFlag.ItemIsSelectable
        item.setFlags(flags)
        return item

    def set_library(self, folders: list[Path], excluded: Iterable[Path] = ()) -> None:
        self._finish_snap()
        before = self.current_scope()
        self._generation += 1
        self._excluded = set(excluded)
        self._created = {
            key
            for key in self._created
            if not any(Path(key) == ex or Path(key).is_relative_to(ex) for ex in self._excluded)
        }
        self._items = {}
        self._children = {}
        self._media_cache = {key: True for key in self._created}
        self.blockSignals(True)
        try:
            self._folders.takeChildren()
            self._folders.setText(0, "Folders" if folders else "Add a folder")
            for folder in folders:
                item = self._add(self._folders, folder.name or str(folder), "folder", folder, root=True)
                item.setToolTip(0, str(folder))
                self._items[str(folder)] = item
            self._roots_requested = False
            if self._folders.isExpanded():
                self._request_root_children()
            self.select_scope(before)
        finally:
            self.blockSignals(False)
        after = self.current_scope()
        if after != before:
            self.scope_selected.emit(after)

    def _request_root_children(self) -> None:
        if self._roots_requested:
            return
        self._roots_requested = True
        for index in range(self._folders.childCount()):
            self._request_children(Path(self._folders.child(index).data(0, PATH_ROLE)))

    def set_counts(self, gallery: int, favorites: int, photos: int = 0, videos: int = 0) -> None:
        counts = {"gallery": gallery, "photos": photos, "videos": videos, "favorites": favorites}
        if counts != self.counts:
            self.counts = counts
            self.viewport().update()

    @staticmethod
    def _depth(item: QTreeWidgetItem) -> int:
        depth = 0
        parent = item.parent()
        while parent is not None:
            depth += 1
            parent = parent.parent()
        return depth

    def current_scope(self) -> Scope:
        item = self.currentItem()
        return self._scope_of(item) if item is not None else Scope.gallery()

    def select_scope(self, scope: Scope) -> None:
        target = self._gallery
        if scope.kind == "photos":
            target = self._photos
        elif scope.kind == "videos":
            target = self._videos
        elif scope.kind == "favorites":
            target = self._favorites
        elif scope.kind == "pc":
            target = self._group
        elif scope.kind == "album":
            target = self._album_items.get(scope.name) or self._gallery
            if target is not self._gallery:
                self._albums.setExpanded(True)
        elif scope.kind == "folder" and scope.path is not None:
            target = self._find_folder(scope.path) or self._gallery
            if target is not self._gallery:
                self._folders.setExpanded(True)
        elif scope.kind == "date":
            found = self._month_items.get((scope.year, scope.month)) if scope.month else self._year_items.get(scope.year)
            target = found or self._gallery
            if found is not None:
                self._date.setExpanded(True)
                if scope.month:
                    found.parent().setExpanded(True)
        elif scope.kind == "place":
            target = self._place_items.get(scope.cell) or self._gallery
            if target is not self._gallery:
                self._places.setExpanded(True)
        self.setCurrentItem(target)

    def _find_folder(self, path: Path) -> QTreeWidgetItem | None:
        for index in range(self._folders.childCount()):
            root = self._folders.child(index)
            root_path = Path(root.data(0, PATH_ROLE))
            if path == root_path:
                return root
            if path.is_relative_to(root_path):
                return self._descend(root, path.relative_to(root_path).parts)
        return None

    def _descend(self, item: QTreeWidgetItem, parts: tuple[str, ...]) -> QTreeWidgetItem | None:
        for part in parts:
            if item.childCount() == 0:
                self._add_placeholder(item)  # discovery may still be running: look now
            item.setExpanded(True)
            self._populate(item)
            item = next(
                (
                    item.child(index)
                    for index in range(item.childCount())
                    if item.child(index).data(0, KIND_ROLE) == "folder"
                    and Path(item.child(index).data(0, PATH_ROLE)).name == part
                ),
                None,
            )
            if item is None:
                return None
        return item

    @staticmethod
    def _scope_of(item: QTreeWidgetItem) -> Scope:
        kind = item.data(0, KIND_ROLE)
        if kind == "photos":
            return Scope.photos()
        if kind == "videos":
            return Scope.videos()
        if kind == "favorites":
            return Scope.favorites()
        if kind in ("year", "month", "place", "album"):
            return item.data(0, SCOPE_ROLE)
        if kind == "group":
            return Scope.pc()
        if kind == "folder":
            return Scope.folder(Path(item.data(0, PATH_ROLE)))
        return Scope.gallery()

    def _add_placeholder(self, item: QTreeWidgetItem) -> None:
        self._add(item, "", "placeholder", selectable=False)

    def _yield_to_ui(self) -> None:
        """Called by the scan threads between folders: stand still while the tree is animating.

        The scans are Python loops, so they share the GIL with the UI thread. Left running during the
        open/close animation they starve it of frames, which is what made the motion look stiff.
        """
        waited = 0.0
        while self._animating.is_set() and waited < 1.5:
            time.sleep(0.01)
            waited += 0.01
        time.sleep(0)  # even when idle, let the UI thread take the GIL between folders

    def _request_children(self, folder: Path) -> None:
        key = str(folder)
        if key in self._children:
            self._apply_children(key)
            return
        self._pool.start(
            ChildrenJob(
                self._generation,
                folder,
                self._media_cache,
                self._signals,
                self._yield_to_ui,
                excluded=self._excluded,
            )
        )

    def _on_children_ready(self, generation: int, key: str, folders: list) -> None:
        if generation != self._generation:
            return  # the library was rebuilt while this was scanning
        self._children[key] = folders
        self._apply_children(key)

    def _apply_children(self, key: str) -> None:
        item = self._items.get(key)
        if item is None or item.childCount() > 0 or not self._children.get(key):
            return
        self._add_placeholder(item)  # makes the row expandable and draws its arrow

    def _populate(self, item: QTreeWidgetItem) -> None:
        if item is self._folders:
            self._request_root_children()
            return
        if item.childCount() != 1 or item.child(0).data(0, KIND_ROLE) != "placeholder":
            return
        item.takeChildren()
        key = item.data(0, PATH_ROLE)
        folders = self._children.get(key)
        if folders is None:
            folders = self._children[key] = media_subfolders(
                Path(key), self._media_cache, excluded=self._excluded
            )
        for folder in folders:
            child = self._add(item, folder.name, "folder", folder)
            self._items[str(folder)] = child
            self._request_children(folder)

    def _on_current_changed(self, current: QTreeWidgetItem | None, previous) -> None:
        self._start_highlight(previous, current)
        if current is not None and current.data(0, KIND_ROLE) in SCOPE_KINDS:
            self.scope_selected.emit(self._scope_of(current))

    # in-place tree edits after a folder operation

    def _remap(self, key: str, old: str, new: str) -> str:
        if key == old:
            return new
        if key.startswith(old + os.sep):
            return new + key[len(old):]
        return key

    def add_created_folder(self, parent: Path, created: Path) -> None:
        pkey, ckey = str(parent), str(created)
        self._created.add(ckey)
        self._media_cache[ckey] = True
        parent_item = self._items.get(pkey)
        if parent_item is None:
            return
        populated = parent_item.childCount() > 0 and parent_item.child(0).data(0, KIND_ROLE) != "placeholder"
        if populated:
            child = self._add(parent_item, created.name, "folder", created)
            self._items[ckey] = child
            self._children.setdefault(pkey, []).append(created)
            self._children[pkey].sort(key=lambda path: path.name.casefold())
            self._place_sorted(parent_item, child)
            self._request_children(created)
        else:
            parent_item.takeChildren()
            self._children[pkey] = media_subfolders(parent, self._media_cache, excluded=self._excluded)
            self._add_placeholder(parent_item)
        parent_item.setExpanded(True)  # reveal the new folder
        self._populate(parent_item)

    def _place_sorted(self, parent_item: QTreeWidgetItem, child: QTreeWidgetItem) -> None:
        """Move `child` to its alphabetical slot among its siblings, keeping everything open."""
        current = self.currentItem()
        opened = self._expanded_in(child)
        name = child.text(0).casefold()
        index = parent_item.indexOfChild(child)
        parent_item.takeChild(index)
        target = parent_item.childCount()
        for position in range(parent_item.childCount()):
            if parent_item.child(position).text(0).casefold() > name:
                target = position
                break
        parent_item.insertChild(target, child)
        for node in opened:
            node.setExpanded(True)
        if current is not None and self.currentItem() is not current:
            self.blockSignals(True)
            self.setCurrentItem(current)
            self.blockSignals(False)

    @staticmethod
    def _expanded_in(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        found = [item] if item.isExpanded() else []
        for index in range(item.childCount()):
            found.extend(NavContentMixin._expanded_in(item.child(index)))
        return found

    def rename_folder(self, old: Path, new: Path) -> None:
        old_key, new_key = str(old), str(new)
        item = self._items.get(old_key)
        if item is None:
            return
        self._items = {self._remap(k, old_key, new_key): v for k, v in self._items.items()}
        self._children = {
            self._remap(k, old_key, new_key): [Path(self._remap(str(p), old_key, new_key)) for p in v]
            for k, v in self._children.items()
        }
        self._media_cache = {self._remap(k, old_key, new_key): v for k, v in self._media_cache.items()}
        self._created = {self._remap(k, old_key, new_key) for k in self._created}
        self._excluded = {Path(self._remap(str(p), old_key, new_key)) for p in self._excluded}
        self._repath(item, old_key, new_key)
        item.setText(0, new.name or str(new))
        if item.data(0, ROOT_ROLE):
            item.setToolTip(0, new_key)
            return
        parent_item = item.parent()
        if parent_item is not None:
            self._children[str(new.parent)] = sorted(
                self._children.get(str(new.parent), []), key=lambda path: path.name.casefold()
            )
            self._place_sorted(parent_item, item)

    def _repath(self, item: QTreeWidgetItem, old: str, new: str) -> None:
        if item.data(0, KIND_ROLE) == "folder":
            item.setData(0, PATH_ROLE, self._remap(item.data(0, PATH_ROLE), old, new))
        for index in range(item.childCount()):
            self._repath(item.child(index), old, new)

    def remove_folder(self, folder: Path) -> None:
        """Drop a (non-root) folder row after it was deleted; fall back to its parent if it was shown."""
        key = str(folder)
        item = self._items.get(key)
        if item is None:
            return
        parent_item = item.parent()
        shown = self.currentItem()
        node = shown
        while node is not None and node is not item:
            node = node.parent()
        if node is item and parent_item is not None:
            self.setCurrentItem(parent_item)  # emits scope_selected for the parent
        if parent_item is None:
            return
        parent_item.removeChild(item)
        prefix = key + os.sep
        for table in (self._items, self._children, self._media_cache):
            for stale in [k for k in table if k == key or k.startswith(prefix)]:
                del table[stale]
        self._created = {k for k in self._created if k != key and not k.startswith(prefix)}
        self._excluded = {p for p in self._excluded if str(p) != key and not str(p).startswith(prefix)}
        parent_key = parent_item.data(0, PATH_ROLE)
        if parent_key in self._children:
            self._children[parent_key] = [p for p in self._children[parent_key] if str(p) != key]