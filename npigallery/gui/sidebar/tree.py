import threading
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QRect, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGraphicsEffect,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from ..common.icons import icon
from ..common.motion import AnimatedMenu, FrameAnimation, SmoothWheelMixin, add_danger_action, decelerate, make_animation
from .children_loader import ChildrenSignals
from .metrics import (
    ACTION_ZONE,
    EXPANDABLE_KINDS,
    FOLDER_ICON_X,
    HIGHLIGHT_DURATION,
    INDENT,
    KIND_ROLE,
    PATH_ROLE,
    ROOT_ROLE,
    ROW_HEIGHT,
    SCOPE_KINDS,
    SCOPE_ROLE,
)
from .nav_delegate import NavDelegate, tone
from .tree_content import NavContentMixin
from .tree_media import NavMediaMixin
from .tree_motion import NavMotionMixin


class BottomFadeEffect(QGraphicsEffect):
    """Makes the bottom edge of the list transparent while rows are hidden below it. Done as an effect
    (not by painting in paintEvent) because a widget cannot be rendered while it is being painted."""

    def __init__(self, tree: "NavTree"):
        super().__init__(tree)
        self._tree = tree

    def draw(self, painter: QPainter) -> None:
        strength = self._tree.bottom_fade()
        if strength <= 0.0:
            self.drawSource(painter)
            return
        result = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        pixmap, offset = result if isinstance(result, tuple) else (result, QPoint())
        if pixmap.isNull():
            return
        ratio = pixmap.devicePixelRatio() or 1.0
        width, height = pixmap.width() / ratio, pixmap.height() / ratio
        top = max(0.0, height - self._tree.FADE_HEIGHT)
        gradient = QLinearGradient(0, top, 0, height)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0, round(255 * (1.0 - strength))))
        mask = QPainter(pixmap)
        mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        mask.fillRect(QRect(0, int(top), int(width) + 1, int(height - top) + 1), gradient)
        mask.end()
        painter.drawPixmap(offset, pixmap)


class NavTree(NavMotionMixin, NavMediaMixin, NavContentMixin, SmoothWheelMixin, QTreeWidget):
    scope_selected = Signal(object)
    nav_clicked = Signal()  # any navigation row was clicked, even the one already shown
    add_folder_requested = Signal()
    remove_folder_requested = Signal(object)
    folder_action_requested = Signal(str, object)  # create | rename | slideshow | explorer | delete
    album_action_requested = Signal(str, str)  # rename | delete, album name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavTree")
        self.compact = False
        self.actions_visible = True  # chevrons and the "This PC" add-folder button
        self.counts: dict[str, int] = {}
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(INDENT)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setUniformRowHeights(False)
        self.setExpandsOnDoubleClick(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)  # per-item steps can't glide
        self.verticalScrollBar().setSingleStep(ROW_HEIGHT)
        self._init_glide()
        self._fade_effect = BottomFadeEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self.verticalScrollBar().valueChanged.connect(lambda *_: self._fade_effect.update())
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self._fade_effect.update())
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setItemDelegate(NavDelegate(self))
        self.setStyleSheet(
            "QTreeWidget#NavTree { background: transparent; outline: 0; }"
            "QTreeWidget#NavTree::branch { image: none; border-image: none; background: transparent; }"
            "QTreeWidget#NavTree::item, QTreeWidget#NavTree::item:selected,"
            " QTreeWidget#NavTree::item:hover { background: transparent; border: none; }"
        )
        # The delegate paints every row itself. Make sure no style/palette path can
        # ever fill a selected row with the system highlight colour.
        palette = self.palette()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
            palette.setColor(group, QPalette.ColorRole.Highlight, Qt.GlobalColor.transparent)
            palette.setColor(group, QPalette.ColorRole.HighlightedText, tone("text"))
        self.setPalette(palette)

        self._hover_index = QModelIndex()
        self._hover_add = False
        self._date_counts: dict[tuple[int, int], int] = {}
        self._place_counts: dict[tuple[float, float], int] = {}
        self._year_items: dict[int, QTreeWidgetItem] = {}
        self._month_items: dict[tuple[int, int], QTreeWidgetItem] = {}
        self._place_items: dict[tuple[float, float], QTreeWidgetItem] = {}
        self._album_counts: list[tuple[str, int]] = []
        self._album_items: dict[str, QTreeWidgetItem] = {}
        self._roots_requested = False
        # Motion state: rows revealing/hiding under an expanding/collapsing folder, and the
        # selection highlight sliding from the previous row to the new one.
        self._reveal: dict[str, float] = {}
        self._reveal_items: list[QTreeWidgetItem] = []
        self._reveal_owner: QTreeWidgetItem | None = None
        self._reveal_closing = False
        self._reveal_animation: FrameAnimation | None = None
        self._collapsing: set[str] = set()
        # Open/close animation is played from two pictures of the list instead of re-laying out
        # and repainting every row on every frame (see _start_snap).
        self._snap: dict | None = None
        self._snap_animation: FrameAnimation | None = None
        self._pre_expand: tuple[str, QPixmap] | None = None
        self._no_highlight = False
        self._suppress_motion = 0
        self._highlight_hidden = False
        self._hl_from: float | None = None
        self._hl_progress = 1.0
        self._hl_animation = make_animation(self, HIGHLIGHT_DURATION, decelerate())
        self._hl_animation.valueChanged.connect(self._on_highlight_step)
        self._hl_animation.finished.connect(self._on_highlight_done)
        # Subfolder discovery: an arrow only appears once a folder is known to have
        # subfolders that contain photos.
        self._generation = 0
        self._items: dict[str, QTreeWidgetItem] = {}
        self._children: dict[str, list[Path]] = {}
        self._media_cache: dict[str, bool] = {}
        self._created: set[str] = set()  # folders made from the menu: listed even while empty
        self._excluded: set[Path] = set()
        self._animating = threading.Event()  # set while a folder opens/closes; read by scan threads
        self._signals = ChildrenSignals(self)
        self._signals.ready.connect(self._on_children_ready)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemExpanded.connect(self._populate)
        self.itemExpanded.connect(self._on_item_expanded)  # after _populate: children exist
        self.currentItemChanged.connect(self._on_current_changed)
        # Only the user scrolling ends the picture: the range changing (a scrollbar appearing because a big
        # folder opened) must not.
        self.verticalScrollBar().actionTriggered.connect(lambda *_: self._finish_snap())

        self._gallery = self._add(None, "Gallery", "gallery")
        self._gallery.setToolTip(0, "Gallery")
        self._photos = self._add(None, "Photos", "photos")
        self._photos.setToolTip(0, "Photos")
        self._videos = self._add(None, "Videos", "videos")
        self._videos.setToolTip(0, "Videos")
        self._favorites = self._add(None, "Favourites", "favorites")
        self._favorites.setToolTip(0, "Favourites")
        self._albums = self._add(None, "Albums", "section", "section:albums", selectable=False)
        self._add(None, "", "separator", selectable=False)
        self._group = self._add(None, "This PC", "group")
        self._group.setToolTip(0, "This PC")
        self._folders = self._add(None, "Folders", "section", "section:folders", selectable=False)
        self._date = self._add(None, "Dates", "section", "section:date", selectable=False)
        self._places = self._add(None, "Locations", "section", "section:places", selectable=False)
        self.sync_section_visibility()
        self.setCurrentItem(self._gallery)

    def drawBranches(self, painter, rect, index) -> None:
        return

    # // motion

    FADE_HEIGHT = 36  # the list melts away over this many pixels above the bottom of the panel

    def bottom_fade(self) -> float:
        """0..1: how strongly the bottom edge fades. Zero when the list fits or is scrolled to its end,
        so the last row is never dimmed; it eases in as more rows hide below."""
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            return 0.0
        return max(0.0, min(1.0, (bar.maximum() - bar.value()) / self.FADE_HEIGHT))

    def paintEvent(self, event) -> None:
        if self._snap is not None:
            self._paint_snapshot()
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_highlight(painter)
        painter.end()
        super().paintEvent(event)

    def drawRow(self, painter: QPainter, option, index: QModelIndex) -> None:
        # Bypass QTreeView's own row/branch/selection background entirely.
        depth = 0
        parent = index.parent()
        while parent.isValid() and not self.compact:  # compact: every icon on one axis
            depth += 1
            parent = parent.parent()
        opt = QStyleOptionViewItem(option)
        opt.rect = QRect(
            depth * self.indentation(),
            option.rect.top(),
            max(0, self.viewport().width() - depth * self.indentation()),
            option.rect.height(),
        )
        opt.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        if self.selectionModel().isSelected(index):
            opt.state |= QStyle.StateFlag.State_Selected
        if index == self._hover_index:
            opt.state |= QStyle.StateFlag.State_MouseOver
        self.itemDelegate().paint(painter, opt, index)

    @property
    def add_hovered(self) -> bool:
        return self._hover_add

    def _in_add_zone(self, point: QPoint) -> bool:
        edge = self.viewport().width() - ACTION_ZONE
        return edge - 24 <= point.x() < edge

    def _set_hover_add(self, hovered: bool) -> None:
        if hovered == self._hover_add:
            return
        self._hover_add = hovered
        self._folders.setToolTip(0, "Add folder" if hovered else "")
        self.viewport().update()

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        index = self.indexAt(point)
        if index != self._hover_index:
            self._hover_index = index
            self.viewport().update()
        self._set_hover_add(
            index.isValid()
            and index.data(KIND_ROLE) == "section"
            and index.data(PATH_ROLE) == "section:folders"
            and self.actions_visible
            and not self.compact
            and self._in_add_zone(point)
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_index = QModelIndex()
        self._set_hover_add(False)
        self.viewport().update()
        super().leaveEvent(event)

    def set_compact(self, compact: bool) -> None:
        self._finish_snap()
        self.compact = compact
        if compact:
            self._folders.setExpanded(True)
        self.sync_section_visibility()
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        snap = self._snap
        if snap is not None:
            size = self.viewport().size()
            # A scrollbar appearing narrows the viewport a little; that is not a real resize.
            slack = self.verticalScrollBar().sizeHint().width() + 4
            if size.height() != snap["size"].height() or abs(size.width() - snap["size"].width()) > slack:
                self._finish_snap()

    def wheelEvent(self, event) -> None:
        self._finish_snap()
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        self._finish_snap()  # hit-testing uses the real (final) layout, so settle the picture first
        point = event.position().toPoint()
        item = self.itemAt(point)
        if event.button() == Qt.MouseButton.RightButton:
            self._without_navigation(super().mousePressEvent, event)
            return
        if item is None or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        kind = item.data(0, KIND_ROLE)
        usable = not self.compact and self.actions_visible
        if kind in ("separator", "placeholder"):
            return
        if kind == "section":
            if usable and item.data(0, PATH_ROLE) == "section:folders":
                if item.childCount() == 0 or self._in_add_zone(point):
                    self.add_folder_requested.emit()
                    return
            if usable:
                self.toggle_item(item)
            return
        if (
            kind in EXPANDABLE_KINDS
            and usable
            and item.childCount() > 0
            and point.x() < self._depth(item) * INDENT + FOLDER_ICON_X
        ):
            self.toggle_item(item)
            return
        was_current = self.currentItem() is item
        super().mousePressEvent(event)
        if kind in SCOPE_KINDS:
            self.nav_clicked.emit()
        can_toggle = not self.compact and self.actions_visible
        if kind in EXPANDABLE_KINDS and can_toggle and item.childCount() > 0:
            # First click only shows this folder's photos (and opens it if closed), so
            # coming back from a subfolder keeps the tree open. Clicking the folder
            # that is already shown toggles its subfolders.
            if was_current or not item.isExpanded():
                self.toggle_item(item)

    def mouseDoubleClickEvent(self, event) -> None:
        # Qt delivers a second press within the system double-click time as a DblClick event, which
        # QAbstractItemView swallows. Without this, opening and then closing a folder (or hopping to
        # the next one) did nothing until the double-click interval had passed.
        if event.button() == Qt.MouseButton.LeftButton:
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._without_navigation(super().mouseReleaseEvent, event)
            return
        super().mouseReleaseEvent(event)

    def _without_navigation(self, handler, event) -> None:
        """Run Qt's default handling but undo any selection change, silently.

        Right-click must only open the context menu: the current tab, the shown photos and
        the expanded folders stay exactly as they were.
        """
        previous = self.currentItem()
        self.blockSignals(True)
        try:
            handler(event)
            if previous is not None and self.currentItem() is not previous:
                self.setCurrentItem(previous)
        finally:
            self.blockSignals(False)
        self.viewport().update()

    def _show_menu(self, position: QPoint) -> None:
        item = self.itemAt(position)
        if item is None:
            return
        if item.data(0, KIND_ROLE) == "section":
            if item.data(0, PATH_ROLE) != "section:folders":
                return
            menu = AnimatedMenu(self)
            add_folder = menu.addAction(icon("plus-circle"), "Add folder")
            if menu.exec(self.viewport().mapToGlobal(position)) is add_folder:
                self.add_folder_requested.emit()
            menu.deleteLater()
            return
        if item.data(0, KIND_ROLE) == "album":
            menu = AnimatedMenu(self)
            rename = menu.addAction(icon("rename"), "Rename")
            menu.addSeparator()
            delete = add_danger_action(menu, "Delete album", "trash")
            chosen = menu.exec(self.viewport().mapToGlobal(position))
            menu.deleteLater()
            name = item.data(0, SCOPE_ROLE).name
            if chosen is rename:
                self.album_action_requested.emit("rename", name)
            elif chosen is delete:
                self.album_action_requested.emit("delete", name)
            return
        if item.data(0, KIND_ROLE) != "folder":
            return
        folder = Path(item.data(0, PATH_ROLE))
        menu = AnimatedMenu(self)
        keys: dict = {}

        def add(key: str, text: str, icon_name: str) -> None:
            keys[menu.addAction(icon(icon_name), text)] = key

        add("create", "Create folder", "folder-plus")
        add("rename", "Rename", "rename")
        add("slideshow", "Start slideshow", "slideshow")
        add("explorer", "Open in File Explorer", "folder-open")
        if item.data(0, ROOT_ROLE):
            add("remove", "Remove from gallery", "close")
        else:
            add("remove", "Hide from gallery", "eye")
        menu.addSeparator()
        delete = add_danger_action(menu, "Delete folder", "trash")

        chosen = menu.exec(self.viewport().mapToGlobal(position))
        key = "delete" if chosen is delete else keys.get(chosen)
        menu.deleteLater()
        if key == "remove":
            self.remove_folder_requested.emit(folder)
        elif key is not None:
            self.folder_action_requested.emit(key, folder)