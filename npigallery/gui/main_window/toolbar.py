from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton, QToolButton

from ...config import ROW_PRESETS
from ..common.icons import icon, icon_with_chevron
from ..common.motion import AnimatedMenu
from ..gallery.model import KindFilter, SortMode
from .top_bar import DropdownToolButton, PILL_HEIGHT


class ToolbarMixin:
    def _build_toolbar(self) -> QFrame:
        pill = QFrame()
        pill.setObjectName("Pill")
        pill.setProperty("dragRegion", True)
        pill.setFixedHeight(PILL_HEIGHT)
        toolbar = QHBoxLayout(pill)
        toolbar.setContentsMargins(5, 0, 5, 0)
        toolbar.setSpacing(2)

        self._select_button = QPushButton(" Select")
        self._select_button.setObjectName("Bordered")
        self._select_button.setIcon(icon("select"))
        self._select_button.setCheckable(True)
        self._select_button.toggled.connect(self._view.set_select_mode)
        toolbar.addWidget(self._select_button)

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setObjectName("Bordered")
        self._cancel_button.setIcon(icon("close"))
        self._cancel_button.clicked.connect(self._view.clear_selection)
        self._cancel_button.hide()
        toolbar.addWidget(self._cancel_button)

        self._delete_button = self._tool_button("trash", "Delete")
        self._delete_button.clicked.connect(self._delete_selected)
        self._delete_button.hide()
        toolbar.addWidget(self._delete_button)

        self._copy_button = self._tool_button("copy", "Copy")
        self._copy_button.clicked.connect(self._copy_selected)
        self._copy_button.hide()
        toolbar.addWidget(self._copy_button)

        self._move_copy_button = DropdownToolButton("folder-move", "Move/copy", "Move or copy")
        move_copy_menu = AnimatedMenu(self)
        move_action = move_copy_menu.addAction(icon("folder"), "Move to folder...")
        move_action.triggered.connect(lambda: self._move_or_copy_selected(move=True))
        copy_to_action = move_copy_menu.addAction(icon("copy"), "Copy to folder...")
        copy_to_action.triggered.connect(lambda: self._move_or_copy_selected(move=False))
        self._set_button_menu(self._move_copy_button, move_copy_menu)
        self._move_copy_button.hide()
        toolbar.addWidget(self._move_copy_button)

        self._album_button = self._tool_button("album", "Add to album", menu=True)
        album_menu = AnimatedMenu(self)
        album_menu.aboutToShow.connect(lambda: self._refill_album_menu(album_menu))  # albums may have changed
        self._set_button_menu(self._album_button, album_menu)
        self._album_button.hide()
        toolbar.addWidget(self._album_button)

        self._album_remove_button = self._tool_button("album-minus", "Remove from album (the files stay on disk)")
        self._album_remove_button.clicked.connect(lambda: self._remove_from_album(self._selected_items()))
        self._album_remove_button.hide()
        toolbar.addWidget(self._album_remove_button)

        divider = QFrame()
        divider.setObjectName("PillDivider")
        divider.setFixedSize(1, 20)
        toolbar.addSpacing(4)
        toolbar.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
        toolbar.addSpacing(4)

        slideshow = self._tool_button("slideshow", "Slideshow")
        slideshow.clicked.connect(self._start_slideshow)
        toolbar.addWidget(slideshow)

        sort_button = self._tool_button("sort", "Sort by", menu=True)
        self._set_button_menu(sort_button, self._build_sort_menu())
        toolbar.addWidget(sort_button)

        filter_button = self._tool_button("filter", "Filter", menu=True)
        self._set_button_menu(filter_button, self._build_filter_menu())
        toolbar.addWidget(filter_button)

        layout_button = self._tool_button("layout", "Thumbnail size", menu=True)
        self._set_button_menu(layout_button, self._build_size_menu())
        toolbar.addWidget(layout_button)

        more = self._tool_button("more", "More")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = AnimatedMenu(self)
        self._select_all_action.setIcon(icon("select-all"))
        self._select_none_action = self._action("Select none	Esc", None, self._view.clear_selection)
        self._select_none_action.setIcon(icon("close"))
        self._refresh_action.setIcon(icon("refresh"))
        more_menu.addAction(self._select_all_action)
        more_menu.addAction(self._select_none_action)
        more_menu.addSeparator()
        more_menu.addAction(self._refresh_action)
        self._set_button_menu(more, more_menu)
        toolbar.addWidget(more)

        settings_button = self._tool_button("settings", "Settings")
        settings_button.clicked.connect(self.show_settings)
        toolbar.addWidget(settings_button)
        return pill

    @staticmethod
    def _set_button_menu(button: QToolButton, menu: QMenu) -> None:
        """Attach a toolbar dropdown; it opens centred under its button."""
        button.setMenu(menu)
        if isinstance(menu, AnimatedMenu):
            menu.centre_on(button)

    def _tool_button(self, name: str, tooltip: str, menu: bool = False) -> QToolButton:
        button = QToolButton()
        button.setObjectName("Tool")
        button.setIcon(icon_with_chevron(name) if menu else icon(name))
        button.setIconSize(QSize(18 + 15, 18) if menu else QSize(18, 18))
        button.setToolTip(tooltip)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    def _add_choice_group(self, menu, entries, current, slot):
        """Radio-style group: (label, value, icon_name) entries with a highlight on the current one."""
        group = QActionGroup(menu)
        group.setExclusive(True)
        actions = []
        for label, value, icon_name in entries:
            action = menu.addAction(icon(icon_name), label)
            action.setCheckable(True)
            action.toggled.connect(
                lambda checked, action=action, name=icon_name: action.setIcon(
                    icon(name, "#ffffff" if checked else "#9a9a9a")
                )
            )
            action.setChecked(value == current)
            action.setIcon(icon(icon_name, "#ffffff" if value == current else "#9a9a9a"))
            action.triggered.connect(lambda _checked=False, value=value: slot(value))
            group.addAction(action)
            actions.append((action, value))
        return actions

    def _exclusive_menu(self, entries, current, slot) -> QMenu:
        menu = AnimatedMenu(self)
        self._add_choice_group(menu, entries, current, slot)
        return menu

    _SORT_KEYS = (("Date", "date", "calendar"), ("Name", "name", "text"), ("Size", "size", "size"))
    _SORT_MODES = {
        ("date", True): SortMode.NEWEST,
        ("date", False): SortMode.OLDEST,
        ("name", False): SortMode.NAME_ASC,
        ("name", True): SortMode.NAME_DESC,
        ("size", True): SortMode.SIZE,
        ("size", False): SortMode.SIZE_ASC,
    }

    def _build_sort_menu(self) -> QMenu:
        current = self._model.sort_mode()
        key, descending = next(
            (pair for pair, mode in self._SORT_MODES.items() if mode is current), ("date", True)
        )
        state = {"key": key, "descending": descending}

        def apply() -> None:
            self._view.begin_transition()
            self._model.set_sort(self._SORT_MODES[(state["key"], state["descending"])])

        def choose_key(value: str) -> None:
            state["key"] = value
            apply()

        def choose_order(value: bool) -> None:
            state["descending"] = value
            apply()

        menu = AnimatedMenu(self)
        self._add_choice_group(menu, list(self._SORT_KEYS), key, choose_key)
        menu.addSeparator()
        self._add_choice_group(
            menu, [("Ascending", False, "arrow-up"), ("Descending", True, "arrow-down")], descending, choose_order
        )
        return menu

    def _build_filter_menu(self) -> QMenu:
        icons = {KindFilter.ALL: "gallery", KindFilter.ORIGINAL: "file-image", KindFilter.PACKED: "box"}
        entries = [(kind.value, kind, icons[kind]) for kind in KindFilter]

        def apply_kind(kind: KindFilter) -> None:
            self._view.begin_transition()
            self._model.set_kind_filter(kind)

        return self._exclusive_menu(entries, self._model.kind_filter(), apply_kind)

    def _build_size_menu(self) -> QMenu:
        current = min(ROW_PRESETS, key=lambda preset: abs(preset[1] - self._view.row_height()))[1]
        glyphs = {"Small": "size-small", "Medium": "size-medium", "Large": "size-large"}
        entries = [(label, height, glyphs.get(label, "size-medium")) for label, height in ROW_PRESETS]
        menu = AnimatedMenu(self)
        shapes = [("River", False, "layout"), ("Square", True, "layout-square")]
        shape_actions = self._add_choice_group(menu, shapes, self._view.square(), self._set_square)
        menu.addSeparator()
        size_actions = self._add_choice_group(menu, entries, current, self._set_row_height)

        def refresh_checks() -> None:
            nearest = min(ROW_PRESETS, key=lambda preset: abs(preset[1] - self._view.row_height()))[1]
            for action, height in size_actions:
                action.setChecked(height == nearest)
            for action, square in shape_actions:
                action.setChecked(square == self._view.square())

        menu.aboutToShow.connect(refresh_checks)
        return menu