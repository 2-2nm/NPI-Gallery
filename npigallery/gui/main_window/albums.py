from PySide6.QtWidgets import QMenu

from ...services.albums import AlbumStore
from ..common.dialogs import ask_text, confirm, warn
from ..common.icons import icon


class AlbumsMixin:
    # albums: named groups of photos that only exist inside the app (nothing moves on disk)

    def _load_albums(self) -> AlbumStore:
        return AlbumStore.from_json(self._settings.value("albums_json", "{}", str))

    def _albums_changed(self, renamed: tuple[str, str] | None = None) -> None:
        """Call after the store changed: save it and bring the gallery, sidebar and header up to date."""
        self._settings.setValue("albums_json", self._albums.to_json())
        self._model.albums_updated()
        self._refresh_album_views(renamed)
        self._update_header()

    def _refresh_album_views(self, renamed: tuple[str, str] | None = None) -> None:
        present = {str(item.path) for item in self._model.library()}
        self._sidebar.tree.set_albums(self._albums.counts(present), renamed)

    # menus

    def _fill_album_menu(self, menu: QMenu, get_items) -> None:
        """Albums to add to, then "New album...". `get_items` is called when an entry is clicked."""
        present = {str(item.path) for item in self._model.library()}
        for name, count in self._albums.counts(present):
            label = f"{name} ({count})".replace("&", "&&")
            action = menu.addAction(icon("album"), label)
            action.triggered.connect(lambda _checked=False, name=name: self._add_items_to_album(name, get_items()))
        if menu.actions():
            menu.addSeparator()
        new_action = menu.addAction(icon("plus-circle"), "New album...")
        new_action.triggered.connect(lambda _checked=False: self._new_album_with(get_items()))

    def _refill_album_menu(self, menu: QMenu) -> None:
        menu.clear()
        self._fill_album_menu(menu, self._selected_items)

    # actions

    def _add_items_to_album(self, name: str, items: list) -> None:
        if items and self._albums.add(name, (str(item.path) for item in items)):
            self._albums_changed()

    def _new_album_with(self, items: list) -> None:
        if not items:
            return
        name = self._ask_album_name("New album", self._albums.unique_name())
        if name is None:
            return
        self._albums.create(name, (str(item.path) for item in items))
        self._albums_changed()

    def _remove_from_album(self, items: list) -> None:
        scope = self._model.scope()
        if scope.kind != "album" or not items:
            return
        self._albums.remove(scope.name, (str(item.path) for item in items))
        self._view.clear_selection()
        self._albums_changed()

    def _on_album_action(self, action: str, name: str) -> None:
        if action == "rename":
            self._rename_album(name)
        elif action == "delete":
            self._delete_album(name)

    def _rename_album(self, name: str) -> None:
        new_name = self._ask_album_name("Rename album", name, current=name)
        if new_name is None or new_name == name:
            return
        if self._albums.rename(name, new_name):
            self._albums_changed(renamed=(name, new_name))

    def _delete_album(self, name: str) -> None:
        if not confirm(
            self,
            "Delete album",
            f"Delete the album “{name}”?\n\nThe photos and videos stay where they are.",
            ok_text="Delete",
            danger=True,
        ):
            return
        if self._albums.delete(name):
            self._albums_changed()

    def _ask_album_name(self, title: str, text: str, current: str | None = None) -> str | None:
        name = ask_text(self, title, "Album name", text)
        if name is None:
            return None
        name = name.strip()
        if not name:
            warn(self, title, "Give the album a name.")
            return None
        taken = self._albums.find(name)
        if taken is not None and taken != current:
            warn(self, title, f"There is already an album called “{taken}”.")
            return None
        return name
