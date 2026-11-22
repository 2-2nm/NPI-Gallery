"""Dialog for Settings > Storage > Duplicate photos: groups of identical photos, one to keep in each.

Any photo of a group can be ticked to be moved to the Recycle Bin (the one marked Keep is only the
suggestion), but the last photo left in a group cannot be ticked, so a group is never wiped out. More groups
load by themselves while scrolling. "Not a duplicate" removes a whole group (or one photo)
from the list for good; right after it an Undo bar brings it back.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from ...filesystem import moves_to_trash
from ...formatting import human_size
from ...services.duplicates import DuplicateResult
from ...services.scanner import MediaItem
from ..common.dialogs import DialogCheck, ElidedLabel, FloatDialog, RoundedThumb, confirm, dialog_label, make_button
from ..common.icons import icon
from ..common.motion import AnimatedMenu, SmoothScrollArea

PAGE_GROUPS = 8  # groups shown when the dialog opens
LOAD_BATCH = 3  # groups added at a time while scrolling (small, so a batch never stalls the scroll)
LOAD_AHEAD = 900  # px from the end of the list at which the next batch is added (before it is in view)
THUMB_THREADS = 2
COLUMNS = 5
THUMB = 128
LIST_HEIGHT = 440


class _ThumbSignals(QObject):
    loaded = Signal(str, object)  # path, QImage | None


class _ThumbTask(QRunnable):
    """Builds one thumbnail off the UI thread; only a QImage crosses over (a QPixmap must stay on the UI thread)."""

    def __init__(self, key: str, item: MediaItem, load: Callable[[MediaItem], QImage | None], signals: _ThumbSignals):
        super().__init__()
        self._key = key
        self._item = item
        self._load = load
        self._signals = signals

    def run(self) -> None:
        try:
            image = self._load(self._item)
        except Exception:
            image = None
        try:
            self._signals.loaded.emit(self._key, image)
        except RuntimeError:
            pass  # the dialog is already gone


class DuplicatesDialog(FloatDialog):
    ignore_group_requested = Signal(str)  # content hash
    unignore_group_requested = Signal(str)
    ignore_file_requested = Signal(str, str)  # path, content hash
    unignore_file_requested = Signal(str)
    show_in_folder_requested = Signal(object)  # MediaItem

    def __init__(
        self,
        result: DuplicateResult,
        thumbnail_for: Callable[[MediaItem], QImage | None],  # runs on a worker thread: must return a QImage
        delete_items: Callable[[list[MediaItem]], set[str]],
        parent=None,
    ):
        super().__init__("Duplicate photos", parent, width=780)
        self._thumbnail_for = thumbnail_for
        self._delete_items = delete_items  # asks the window to delete; returns the paths that are gone
        self._groups: list[list] = [[group.sha256, list(group.items)] for group in result.groups]
        self._shown = min(PAGE_GROUPS, len(self._groups))
        self._checks: dict[str, tuple[DialogCheck, MediaItem]] = {}
        self._keep_paths: set[str] = set()
        self._group_paths: list[list[str]] = []
        self._pixmaps: dict[str, QPixmap | None] = {}
        self._thumb_widgets: dict[str, list[RoundedThumb]] = {}  # waiting for their picture
        self._thumb_requested: set[str] = set()
        self._thumb_signals = _ThumbSignals(self)
        self._thumb_signals.loaded.connect(self._on_thumb_loaded)
        self._thumb_pool = QThreadPool(self)
        self._thumb_pool.setMaxThreadCount(THUMB_THREADS)
        self._undo: tuple | None = None

        self._summary = dialog_label("", "detail", wrap=True)
        self._summary.setFixedHeight(self._summary.fontMetrics().height() + 4)
        self.body.addWidget(self._summary)

        self._scroll = SmoothScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(LIST_HEIGHT)
        container = QWidget()
        self._list = QVBoxLayout(container)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(8)
        self._scroll.setWidget(container)
        bar = self._scroll.verticalScrollBar()
        bar.valueChanged.connect(self._maybe_load_more)
        bar.rangeChanged.connect(self._maybe_load_more)
        self.body.addWidget(self._scroll)

        self._empty = dialog_label("No duplicates to review.", "detail")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setMinimumHeight(LIST_HEIGHT)
        self.body.addWidget(self._empty)

        self._undo_bar = QWidget()
        undo_row = QHBoxLayout(self._undo_bar)
        undo_row.setContentsMargins(0, 0, 0, 0)
        self._undo_label = dialog_label("", "detail")
        undo_button = make_button("Undo")
        undo_button.clicked.connect(self._undo_last)
        undo_row.addWidget(self._undo_label, 1)
        undo_row.addWidget(undo_button)
        policy = self._undo_bar.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # showing / hiding Undo must not resize the dialog
        self._undo_bar.setSizePolicy(policy)
        self._undo_bar.setVisible(False)
        self.body.addWidget(self._undo_bar)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self._selected_label = dialog_label("", "detail")
        self._select_button = make_button("Select all copies")
        self._select_button.clicked.connect(self._toggle_selection)
        self._delete_button = make_button("Move to Recycle Bin" if moves_to_trash() else "Delete", "danger", "trash")
        self._delete_button.clicked.connect(self._on_delete)
        footer.addWidget(self._selected_label, 1)
        footer.addWidget(self._select_button)
        footer.addWidget(self._delete_button)
        self.body.addLayout(footer)

        self._render()
        self.finish()

    # // building the list

    def _render(self) -> None:
        """(Re)build the visible group cards, keeping what is ticked."""
        ticked = {path for path, (check, _item) in self._checks.items() if check.isChecked()}
        bar = self._scroll.verticalScrollBar()
        scrolled = bar.value()
        container = self._scroll.widget()
        container.setUpdatesEnabled(False)
        self._checks.clear()
        self._keep_paths.clear()
        self._group_paths = []
        self._thumb_widgets.clear()  # the old cards are going away; the new ones register themselves
        while self._list.count():
            entry = self._list.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.hide()  # gone at once, not at the next event loop turn
                widget.setParent(None)
                widget.deleteLater()
        self._shown = min(max(self._shown, PAGE_GROUPS), len(self._groups))
        for sha, items in self._groups[: self._shown]:
            self._list.addWidget(self._group_card(sha, items))
        self._list.addStretch(1)
        for path, (check, _item) in self._checks.items():
            check.blockSignals(True)
            check.setChecked(path in ticked)
            check.blockSignals(False)
        container.setUpdatesEnabled(True)
        self._sync()
        QTimer.singleShot(0, lambda: bar.setValue(scrolled))  # after the layout: the list stays where it was
        QTimer.singleShot(0, self._maybe_load_more)

    def _maybe_load_more(self, *_args) -> None:
        """Add the next batch of groups when the list is short or scrolled close to its end."""
        if self._shown >= len(self._groups):
            return
        # Measured from the layout's own height, so it does not matter whether the list has been laid out yet.
        viewport = self._scroll.viewport().height() or LIST_HEIGHT
        scrolled = self._scroll.verticalScrollBar().value()
        if self._list.sizeHint().height() - (scrolled + viewport) >= LOAD_AHEAD:
            return
        stop = min(self._shown + LOAD_BATCH, len(self._groups))
        container = self._scroll.widget()
        container.setUpdatesEnabled(False)  # new cards are painted once, already laid out
        self._list.takeAt(self._list.count() - 1)  # the stretch at the end
        for sha, items in self._groups[self._shown : stop]:
            self._list.addWidget(self._group_card(sha, items))
        self._list.addStretch(1)
        self._shown = stop
        self._list.activate()
        container.setUpdatesEnabled(True)
        self._sync()
        QTimer.singleShot(0, self._maybe_load_more)  # still short of a full screen? keep going

    def _group_card(self, sha: str, items: list[MediaItem]) -> QFrame:
        card = QFrame()
        card.setObjectName("SettingCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel(f"{len(items)} identical photos · {human_size(items[0].file_size)} each")
        title.setObjectName("CardTitle")
        not_duplicate = make_button("Not a duplicate")
        not_duplicate.clicked.connect(lambda _checked=False, s=sha: self._ignore_group(s))
        header.addWidget(title, 1)
        header.addWidget(not_duplicate)
        layout.addLayout(header)

        self._group_paths.append([str(item.path) for item in items])
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)
        for index, item in enumerate(items):
            grid.addWidget(self._photo_cell(sha, item, keep=index == 0), index // COLUMNS, index % COLUMNS)
        for column in range(COLUMNS):
            grid.setColumnStretch(column, 0)
        grid.setColumnStretch(COLUMNS, 1)
        layout.addLayout(grid)
        return card

    def _photo_cell(self, sha: str, item: MediaItem, keep: bool) -> QWidget:
        key = str(item.path)
        cell = QWidget()
        cell.setFixedWidth(THUMB)
        column = QVBoxLayout(cell)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)

        thumb = RoundedThumb(THUMB, 10)
        if key in self._pixmaps:
            thumb.set_pixmap(self._pixmaps[key])
        else:  # shows the placeholder now, the picture arrives from a worker thread
            self._thumb_widgets.setdefault(key, []).append(thumb)
            if key not in self._thumb_requested:
                self._thumb_requested.add(key)
                self._thumb_pool.start(_ThumbTask(key, item, self._thumbnail_for, self._thumb_signals))
        column.addWidget(thumb)
        column.addWidget(dialog_label("Keep" if keep else " ", "name"))
        column.addWidget(ElidedLabel(item.name, "CardTitle"))
        column.addWidget(ElidedLabel(str(item.path.parent), "CardDesc"))

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(4)
        if keep:
            self._keep_paths.add(key)
        check = DialogCheck("Remove")
        check.toggled.connect(lambda _on: self._sync())
        self._checks[key] = (check, item)
        bottom.addWidget(check, 1)
        more = QToolButton()
        more.setObjectName("DialogClose")
        more.setFixedSize(28, 28)
        more.setIcon(icon("more", None, 16))
        more.setIconSize(QSize(16, 16))
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.setToolTip("More")
        more.clicked.connect(lambda _checked=False, b=more, s=sha, i=item: self._photo_menu(b, s, i))
        bottom.addWidget(more)
        column.addLayout(bottom)
        return cell

    def _on_thumb_loaded(self, key: str, image) -> None:
        pixmap = QPixmap.fromImage(image) if isinstance(image, QImage) and not image.isNull() else None
        self._pixmaps[key] = pixmap
        for thumb in self._thumb_widgets.pop(key, []):
            if isValid(thumb):
                thumb.set_pixmap(pixmap)

    def done(self, result: int) -> None:
        self._thumb_pool.clear()  # thumbnails not started yet are dropped
        super().done(result)

    def _photo_menu(self, button: QToolButton, sha: str, item: MediaItem) -> None:
        menu = AnimatedMenu(self)
        folder = menu.addAction(icon("folder-open"), "Show in folder")
        ignore = menu.addAction(icon("check"), "Not a duplicate")
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen is folder:
            self.show_in_folder_requested.emit(item)
        elif chosen is ignore:
            self._ignore_file(sha, item)

    # // summary and buttons

    def _sync(self) -> None:
        count = len(self._groups)
        freeable = sum(items[0].file_size * (len(items) - 1) for _sha, items in self._groups)
        if count:
            noun = "group" if count == 1 else "groups"
            self._summary.setText(f"{count} {noun} of identical photos. Removing the copies frees {human_size(freeable)}.")
        else:
            self._summary.setText("")
        self._scroll.setVisible(count > 0)
        self._empty.setVisible(count == 0)
        self._guard_last_photo()
        chosen = self._chosen()
        if chosen:
            size = sum(item.file_size for item in chosen)
            self._selected_label.setText(f"{len(chosen)} selected · {human_size(size)}")
        else:
            self._selected_label.setText("Tick the photos to remove. One photo of each group always stays.")
        self._delete_button.setEnabled(bool(chosen))
        every_copy = self._every_copy_ticked()
        self._select_button.setText("Clear selection" if every_copy else "Select all copies")
        self._select_button.setEnabled(bool(self._checks))

    def _every_copy_ticked(self) -> bool:
        copies = [check for path, (check, _item) in self._checks.items() if path not in self._keep_paths]
        return bool(copies) and all(check.isChecked() for check in copies)

    def _guard_last_photo(self) -> None:
        """When a group has a single photo left unticked, that one cannot be ticked: a group is never wiped out."""
        for paths in self._group_paths:
            unticked = [path for path in paths if path in self._checks and not self._checks[path][0].isChecked()]
            for path in paths:
                check = self._checks.get(path)
                if check is None:
                    continue
                last = len(unticked) == 1 and path == unticked[0]
                check[0].setEnabled(not last)
                check[0].setToolTip("At least one photo of the group has to stay" if last else "")

    def _chosen(self) -> list[MediaItem]:
        return [item for check, item in self._checks.values() if check.isChecked()]

    def _toggle_selection(self) -> None:
        clear = self._every_copy_ticked()
        for path, (check, _item) in self._checks.items():
            check.blockSignals(True)
            check.setChecked(not clear and path not in self._keep_paths)
            check.blockSignals(False)
            check.update()
        self._sync()

    # // deleting

    def _on_delete(self) -> None:
        items = self._chosen()
        if not items:
            return
        where = "to the Recycle Bin" if moves_to_trash() else "permanently"
        noun = "photo" if len(items) == 1 else "photos"
        if not confirm(
            self,
            "Remove duplicates",
            f"Move {len(items)} {noun} {where}? At least one photo of each group stays.",
            ok_text="Move to Recycle Bin" if moves_to_trash() else "Delete",
            danger=True,
        ):
            return
        gone = self._delete_items(items)
        if gone:
            self.remove_paths(gone)

    def remove_paths(self, paths: set[str]) -> None:
        """Photos that are gone from disk leave their groups; a group with one photo left is over."""
        kept = []
        for sha, items in self._groups:
            items = [item for item in items if str(item.path) not in paths]
            if len(items) > 1:
                kept.append([sha, items])
        self._groups = kept
        self._undo = None
        self._undo_bar.setVisible(False)
        self._render()

    # // not a duplicate

    def _ignore_group(self, sha: str) -> None:
        position = next((i for i, (s, _items) in enumerate(self._groups) if s == sha), None)
        if position is None:
            return
        items = self._groups[position][1]
        del self._groups[position]
        self.ignore_group_requested.emit(sha)
        self._offer_undo("Group marked as not a duplicate.", ("group", sha, position, items))
        self._render()

    def _ignore_file(self, sha: str, item: MediaItem) -> None:
        position = next((i for i, (s, _items) in enumerate(self._groups) if s == sha), None)
        if position is None:
            return
        before = list(self._groups[position][1])
        rest = [other for other in before if other.path != item.path]
        if len(rest) > 1:
            self._groups[position][1] = rest
        else:
            del self._groups[position]
        self.ignore_file_requested.emit(str(item.path), sha)
        self._offer_undo("Photo marked as not a duplicate.", ("file", sha, position, before, str(item.path)))
        self._render()

    def _offer_undo(self, text: str, action: tuple) -> None:
        self._undo = action
        self._undo_label.setText(text)
        self._undo_bar.setVisible(True)

    def _undo_last(self) -> None:
        action, self._undo = self._undo, None
        self._undo_bar.setVisible(False)
        if action is None:
            return
        if action[0] == "group":
            _kind, sha, position, items = action
            self.unignore_group_requested.emit(sha)
        else:
            _kind, sha, position, items, path = action
            self.unignore_file_requested.emit(path)
            self._groups = [entry for entry in self._groups if entry[0] != sha]
        items = [item for item in items if item.path.exists()]
        if len(items) > 1:
            self._groups.insert(min(position, len(self._groups)), [sha, items])
        self._render()