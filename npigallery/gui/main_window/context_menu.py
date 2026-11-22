from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction, QKeySequence

from ...services.wallpaper import is_supported as wallpaper_supported
from ..common.icons import icon
from ..common.motion import AnimatedMenu, PHOTO_MENU_DURATION, PHOTO_MENU_SLIDE, add_danger_action


class ContextMenuMixin:
    # photo right-click context menu

    def build_photo_context_menu(self, row: int, items: list) -> AnimatedMenu:
        menu = AnimatedMenu(self, duration=PHOTO_MENU_DURATION, slide=PHOTO_MENU_SLIDE)
        single = len(items) == 1
        photo = single and not items[0].is_video  # edit / background / wallpaper only make sense for a photo

        def add_item(
            text: str, icon_name: str, slot, shortcut: str | tuple | None = None, enabled: bool = True
        ) -> QAction:
            action = menu.addAction(icon(icon_name), text)
            if shortcut is not None:
                keys = shortcut if isinstance(shortcut, tuple) else (shortcut,)
                action.setShortcuts([QKeySequence(key) for key in keys])  # first one is the label
            action.setEnabled(enabled)
            action.triggered.connect(slot)
            return action

        add_item("Open", "image-open", lambda: self._open_viewer(row), shortcut=("Enter", "Return"))
        add_item("Edit", "edit-image", lambda: self._edit_items(items), shortcut="Ctrl+E", enabled=photo)
        add_item("Start slideshow", "slideshow", lambda: self._start_slideshow_at(row), shortcut="F5")

        menu.addSeparator()

        add_item("Copy", "copy", lambda: self._copy_items(items), shortcut="Ctrl+C")
        add_item("Copy as path", "copy-path", lambda: self._copy_paths(items), shortcut="Ctrl+Shift+C")
        add_item("Share", "share", lambda: self._share_items(items), shortcut="Alt+S")
        add_item(
            "Set as sidebar background",
            "gallery",
            lambda: self._set_sidebar_background(items[0]),
            enabled=photo,
        )
        add_item(
            "Set as background",
            "gallery",
            lambda: self._set_window_background(items[0]),
            enabled=photo,
        )
        if wallpaper_supported():
            add_item(
                "Set as wallpaper",
                "wallpaper",
                lambda: self._set_wallpaper(items[0]),
                shortcut="Ctrl+B",
                enabled=photo,
            )

        menu.addSeparator()

        move_copy_menu = AnimatedMenu(menu, duration=150, slide=8)
        move_copy_menu.setTitle("Move/Copy")
        move_copy_menu.setIcon(icon("folder-move"))
        copy_to_action = move_copy_menu.addAction(icon("copy"), "Copy to folder")
        copy_to_action.triggered.connect(lambda: self._move_or_copy_items(items, move=False))
        move_to_action = move_copy_menu.addAction(icon("folder-move"), "Move to folder")
        move_to_action.triggered.connect(lambda: self._move_or_copy_items(items, move=True))
        menu.addMenu(move_copy_menu)

        album_menu = AnimatedMenu(menu, duration=150, slide=8)
        album_menu.setTitle("Add to album")
        album_menu.setIcon(icon("album"))
        self._fill_album_menu(album_menu, lambda: items)
        menu.addMenu(album_menu)
        if self._model.scope().kind == "album":
            add_item("Remove from album", "album-minus", lambda: self._remove_from_album(items))

        add_item("Rename", "rename", lambda: self._rename_item(items[0]), shortcut="F2", enabled=single)
        add_item(
            "Open in File Explorer",
            "folder-open",
            lambda: self._open_item_in_explorer(items[0]),
            shortcut="F3",
            enabled=single,
        )
        add_item(
            "Go to folder in NPI Gallery",
            "folder-search",
            lambda: self._go_to_item_folder(items[0]),
            enabled=single,
        )

        menu.addSeparator()

        delete_action = add_danger_action(menu, "Delete", "trash", "Delete")
        delete_action.triggered.connect(lambda: self._delete_items(items))
        return menu

    def _show_photo_context_menu(self, row: int, global_pos: QPoint) -> None:
        if row < 0 or row >= self._model.rowCount():
            return
        selected = self._view.selected_rows()
        if len(selected) > 1 and row in selected:
            items = [self._model.item_at(r) for r in selected]
        else:
            items = [self._model.item_at(row)]

        menu = self.build_photo_context_menu(row, items)
        self._view.set_context_target(row)
        try:
            menu.exec(global_pos)
        finally:
            self._view.set_context_target(None)
            menu.deleteLater()