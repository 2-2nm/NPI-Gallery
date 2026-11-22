import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import profiling
from ...config import APP_NAME, APP_TITLE, DEFAULT_ROW_HEIGHT, media_index_path
from ...services.index import MediaIndex
from ...services.thumbcache import ThumbnailCache
from ..backdrop import BackdropRoot, backdrop
from ..backdrop import system as backdrop_system
from ..batch.dialog import BatchDialog, BatchMode
from ..common.icons import icon, themed_pixmap
from ..common.motion import PageTransition
from ..common.scope import Scope, date_label, place_label
from ..gallery.model import GalleryModel
from ..gallery.scan_worker import ScanWorker
from ..gallery.thumbnails import ThumbnailLoader
from ..gallery.view import GalleryView
from ..settings.page import SettingsPage
from ..sidebar.metrics import DEFAULT_EXPANDED_WIDTH
from ..sidebar.panel import Sidebar
from ..theme import accent, theme
from ..theme.preferences import prefs
from .albums import AlbumsMixin
from .context_menu import ContextMenuMixin
from .duplicates import DuplicatesMixin
from .frameless import FramelessMixin, IS_WINDOWS
from .item_actions import ItemActionsMixin
from .library import LibraryMixin
from .scanning import ScanningMixin
from .toolbar import ToolbarMixin
from .top_bar import PILL_HEIGHT, TopBar
from .viewing import ViewingMixin


SW_MAXIMIZE = 3


class MainWindow(
    FramelessMixin,
    ToolbarMixin,
    LibraryMixin,
    AlbumsMixin,
    ScanningMixin,
    ItemActionsMixin,
    ContextMenuMixin,
    DuplicatesMixin,
    ViewingMixin,
    QMainWindow,
):
    def __init__(self, cache: ThumbnailCache, start_folder: Path | None = None):
        super().__init__()
        self._settings_mode = False
        if backdrop_system.translucent_supported():
            # Must be set before the native window exists. The window root paints everything opaque unless the
            # background is Acrylic, so this changes nothing visible; it lets Acrylic switch on without a restart.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if not IS_WINDOWS:
            # Windows keeps its native frame (animations, snap) and only hides the
            # native title bar in nativeEvent; other platforms fall back to frameless.
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle(APP_TITLE)
        self._normal_rect: QRect | None = None  # last size / place the user gave the window while it was not maximized
        self._fullscreen_from_maximized = False  # where F11 returns to
        self._want_maximized = False
        self._restoring = False
        self._seen_zoomed = False  # True once this run has actually shown the window maximized
        self._restore_attempts = 0
        self._f11_active = False
        self._prev_zoomed = False  # IsZoomed as of the previous state change: _normal_rect is only enforced on zoomed -> normal
        self.resize(1360, 860)
        self.setMinimumSize(900, 540)
        self._settings = QSettings(APP_NAME, APP_NAME)
        self._restore_window_state()
        self._cache = cache
        self._index = MediaIndex(media_index_path())
        self._init_duplicates()
        self._library: list[Path] = self._load_library(start_folder)
        self._excluded: list[Path] = self._load_excluded()
        self._scan_worker: ScanWorker | None = None
        self._scanning = False
        self._known: list = []

        self._viewers: list = []
        self._loader = ThumbnailLoader(cache, self)
        self._model = GalleryModel(self._loader, self)
        self._model.set_favorites(self._load_favorites())
        self._model.favorites_changed.connect(self._save_favorites)
        self._albums = self._load_albums()
        self._model.set_albums(self._albums)
        self._view = GalleryView(self)
        self._view.setModel(self._model)
        self._view.set_row_height(self._settings.value("row_height", DEFAULT_ROW_HEIGHT, int))
        self._view.set_square(self._settings.value("tile_square", False, bool))
        self._view.activated.connect(self._open_viewer)
        self._view.selection_changed.connect(self._on_selection_changed)
        self._view.row_height_changed.connect(self._remember_row_height)
        self._view.context_menu_requested.connect(self._show_photo_context_menu)
        self._model.modelReset.connect(self._update_header)

        self._search = QLineEdit()
        self._search.setObjectName("Search")
        self._search.setPlaceholderText("Search in Gallery")
        self._search.setClearButtonEnabled(True)
        self._search_action = self._search.addAction(
            icon("search", "#b8b8b8", 15), QLineEdit.ActionPosition.LeadingPosition
        )
        self._search.textChanged.connect(self._on_search_changed)

        self._sidebar = Sidebar(self)
        saved_sidebar_width = self._settings.value("sidebar_width", DEFAULT_EXPANDED_WIDTH, int)
        self._sidebar.set_expanded_width(saved_sidebar_width, persist=False)
        self._sidebar.width_changed.connect(self._remember_sidebar_width)
        self._sidebar.width_animation_started.connect(self._on_sidebar_width_animation)
        self._sidebar.tree.set_library(self._library, self._excluded)
        self._sidebar.tree.scope_selected.connect(self._on_scope_selected)
        self._sidebar.tree.add_folder_requested.connect(self._add_folder)
        self._sidebar.tree.remove_folder_requested.connect(self._remove_folder)
        self._sidebar.tree.folder_action_requested.connect(self._on_folder_action)
        self._sidebar.tree.album_action_requested.connect(self._on_album_action)
        self._model.modelReset.connect(self._update_counts)
        self._model.favorites_changed.connect(self._update_counts)
        self._model.library_changed.connect(self._update_library_views)

        self._build_actions()
        title_pill = self._build_title_pill()
        self._toolbar_pill = self._build_toolbar()
        self._top_bar = top_bar = TopBar(self._search, self, title_pill, self._toolbar_pill)

        root = BackdropRoot()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(top_bar)
        self._gallery_page = self._build_content()
        self._settings_page = SettingsPage()
        self._settings_page.set_center_reference(self._sidebar)  # category list is centred on the sidebar
        self._settings_page.clear_cache_requested.connect(self._clear_cache)
        self._settings_page.dump_requested.connect(lambda: self._open_batch(BatchMode.DUMP))
        self._settings_page.restore_requested.connect(lambda: self._open_batch(BatchMode.RESTORE))
        self._settings_page.restore_folder_requested.connect(self._restore_folder)
        self._settings_page.set_hidden_folders(self._visible_excluded())
        self._connect_duplicates()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._gallery_page)
        self._stack.addWidget(self._settings_page)
        self._transition = PageTransition(self._stack, background=backdrop().paint)
        column.addWidget(self._stack, 1)
        outer.addLayout(column, 1)
        self.setCentralWidget(root)
        backdrop().changed.connect(self._view.viewport().update)

        self._sidebar.tree.nav_clicked.connect(self.show_gallery)
        prefs().changed.connect(self._on_preference_changed)
        # the slider fires on every step; one restyle per pause is enough
        self._float_opacity_timer = QTimer(self)
        self._float_opacity_timer.setSingleShot(True)
        self._float_opacity_timer.setInterval(60)
        self._float_opacity_timer.timeout.connect(lambda: theme.refresh_stylesheet(QApplication.instance()))
        theme.signals().changed.connect(self._on_theme_applied)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
        if hasattr(hints, "accentColorChanged"):
            hints.accentColorChanged.connect(self._on_system_accent_changed)

        self._grips = self._build_grips()
        self._top_bar.sync_maximized()
        with profiling.span("_show_cached (index load + model + first layout)"):
            self._show_cached()
        self._view.setFocus()
        self._scan_deferred = bool(self._known)  # see ScanningMixin._first_scan
        QTimer.singleShot(0, self._first_scan)
        QTimer.singleShot(250, self._model.prewarm)
        QGuiApplication.instance().applicationStateChanged.connect(self._on_application_state)
        QGuiApplication.instance().applicationStateChanged.connect(self._refresh_backdrop_system)

    # construction

    def _build_actions(self) -> None:
        self._refresh_action = self._action("Refresh", "Ctrl+Shift+R", self.reload)
        self._dump_action = self._action("Dump folder to .npi...", "Ctrl+D", lambda: self._open_batch(BatchMode.DUMP))
        self._restore_action = self._action(
            "Restore folder from .npi...", "Ctrl+R", lambda: self._open_batch(BatchMode.RESTORE)
        )
        self._select_all_action = self._action("Select all", QKeySequence.StandardKey.SelectAll, self._view.select_all)
        self._delete_action = self._action("Delete", QKeySequence.StandardKey.Delete, self._delete_active)
        self._edit_action = self._action("Edit", "Ctrl+E", self._edit_active)
        self._slideshow_action = self._action("Start slideshow", "F5", self._slideshow_active)
        self._copy_action = self._action("Copy", "Ctrl+C", self._copy_active)
        self._copy_path_action = self._action("Copy as path", "Ctrl+Shift+C", self._copy_path_active)
        self._share_action = self._action("Share", "Alt+S", self._share_active)
        self._wallpaper_action = self._action("Set as wallpaper", "Ctrl+B", self._wallpaper_active)
        self._rename_action = self._action("Rename", "F2", self._rename_active)
        self._explorer_action = self._action("Open in File Explorer", "F3", self._explorer_active)
        self._fullscreen_action = self._action("Full screen", "F11", self.toggle_fullscreen)
        for action in (
            self._select_all_action,
            self._delete_action,
            self._refresh_action,
            self._dump_action,
            self._restore_action,
            self._edit_action,
            self._slideshow_action,
            self._copy_action,
            self._copy_path_action,
            self._share_action,
            self._wallpaper_action,
            self._rename_action,
            self._explorer_action,
            self._fullscreen_action,
        ):
            self.addAction(action)

    def _action(self, text: str, shortcut, slot) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("Content")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._view, 1)
        return content

    def _build_title_pill(self) -> QFrame:
        pill = QFrame()
        pill.setObjectName("Pill")
        pill.setProperty("dragRegion", True)
        pill.setFixedHeight(PILL_HEIGHT)

        self._title_icon = QLabel()
        self._title_holder = QFrame()
        self._title_holder.setObjectName("PillIconHolder")
        self._title_holder.setProperty("dragRegion", True)
        self._title_holder.setFixedSize(36, 36)
        holder_layout = QHBoxLayout(self._title_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(self._title_icon, 0, Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel("Gallery")
        self._title.setObjectName("PillTitle")
        self._subtitle = QLabel()
        self._subtitle.setObjectName("PillSubtitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        texts.addWidget(self._title)
        texts.addWidget(self._subtitle)

        layout = QHBoxLayout(pill)
        layout.setContentsMargins(5, 0, 24, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title_holder)
        layout.addLayout(texts)
        self._title_layout = layout
        return pill

    def _sync_title_margins(self) -> None:
        # The 5px left margin assumes the 36px icon holder; without it the text hugs the pill edge.
        left = 5 if self._title_holder.isVisibleTo(self._title_holder.parentWidget()) else 24
        self._title_layout.setContentsMargins(left, 0, 24, 0)

    # settings page

    def settings_visible(self) -> bool:
        return self._stack.currentWidget() is self._settings_page

    def show_settings(self) -> None:
        if self.settings_visible():
            return
        self._sidebar.tree.set_highlight_hidden(True)
        self._search.setPlaceholderText("Search in Gallery")
        self._settings_mode = True
        self._toolbar_pill.hide()
        self._update_header()
        self._refresh_cache_usage()
        self._apply_duplicates()
        self._settings_page.set_hidden_folders(self._visible_excluded())
        self._transition.switch_to(self._stack.indexOf(self._settings_page))

    def show_gallery(self) -> None:
        if not self.settings_visible():
            return
        self._sidebar.tree.set_highlight_hidden(False)
        self._settings_mode = False
        self._toolbar_pill.show()
        self._update_header()
        self._transition.switch_to(self._stack.indexOf(self._gallery_page))
        self._view.setFocus()

    def backdrop_size(self) -> QSize:
        """Size of the area the window background picture is framed to (the crop dialog uses its shape)."""
        return self.centralWidget().size()

    def _on_preference_changed(self, key: str) -> None:
        if key == "theme":
            theme.apply_theme(QApplication.instance(), prefs().get("theme"))
        elif key in accent.APPLY_KEYS or (key in accent.SIDEBAR_KEYS and accent.current_mode() == accent.MODE_AUTOMATIC):
            theme.apply_theme(QApplication.instance())
        elif key == "float_opacity":
            self._float_opacity_timer.start()
        elif key == "tile_attributes":
            self._view.viewport().update()

    def _on_system_scheme_changed(self, *_args) -> None:
        if theme.mode() == "system":
            theme.apply_theme(QApplication.instance())

    def _on_system_accent_changed(self, *_args) -> None:
        if accent.current_mode() == accent.MODE_SYSTEM:
            theme.apply_theme(QApplication.instance())

    def _on_system_colors_changed(self) -> None:
        backdrop().refresh_system()  # Transparency effects is part of the same Windows personalization settings
        if theme.mode() == "system" or accent.current_mode() == accent.MODE_SYSTEM:
            theme.apply_theme(QApplication.instance())

    def _refresh_backdrop_system(self, state) -> None:
        """Back in the app: the wallpaper or Transparency effects may have changed while it was in the background."""
        if state == Qt.ApplicationState.ApplicationActive:
            backdrop().refresh_system()

    def _on_theme_applied(self) -> None:
        self._update_header()
        self._view.viewport().update()
        self._sidebar.tree.viewport().update()
        self.update()

    # header state

    def _on_search_changed(self, text: str) -> None:
        if text:
            self.show_gallery()
        self._model.set_filter(text)

    def _on_sidebar_width_animation(self, start: int, end: int) -> None:
        # The photo area gains what the sidebar gives up (and loses it when the sidebar opens).
        self._view.begin_width_flip(start - end)

    def _on_scope_selected(self, scope: Scope) -> None:
        self.show_gallery()
        self._view.begin_transition()
        self._model.set_scope(scope)
        if scope.kind == "photos":
            placeholder = "Search in Photos"
        elif scope.kind == "videos":
            placeholder = "Search in Videos"
        elif scope.kind == "favorites":
            placeholder = "Search in Favourites"
        elif scope.kind == "album":
            placeholder = f"Search in {scope.name}"
        elif scope.kind == "pc":
            placeholder = "Search in This PC"
        elif scope.kind == "folder" and scope.path is not None:
            placeholder = f"Search in {scope.path.name or scope.path}"
        elif scope.kind == "date":
            placeholder = f"Search in {date_label(scope.year, scope.month)}"
        elif scope.kind == "place" and scope.cell is not None:
            placeholder = f"Search in {place_label(scope.cell)}"
        else:
            placeholder = "Search in Gallery"
        self._search.setPlaceholderText(placeholder)
        self._update_header()
        self._sync_toolbar(self._view.is_selecting())

    def _on_selection_changed(self) -> None:
        selecting = self._view.is_selecting()
        if self._select_button.isChecked() != selecting:
            self._select_button.blockSignals(True)
            self._select_button.setChecked(selecting)
            self._select_button.blockSignals(False)
        self._sync_toolbar(selecting)
        self._update_header()

    def _sync_toolbar(self, selecting: bool) -> None:
        has_selection = bool(self._view.selected_rows())
        self._select_button.setVisible(not selecting)
        self._cancel_button.setVisible(selecting)
        self._delete_button.setVisible(selecting)
        self._copy_button.setVisible(selecting)
        self._move_copy_button.setVisible(selecting)
        self._album_button.setVisible(selecting)
        self._album_remove_button.setVisible(selecting and self._model.scope().kind == "album")
        self._delete_button.setEnabled(has_selection)
        self._copy_button.setEnabled(has_selection)
        self._move_copy_button.setEnabled(has_selection)
        self._album_button.setEnabled(has_selection)
        self._album_remove_button.setEnabled(has_selection)

    def _update_library_views(self) -> None:
        self._sidebar.tree.set_media(self._model.library())
        self._refresh_album_views()

    def _update_counts(self) -> None:
        videos = self._model.library_video_count()
        total = self._model.total_count()
        self._sidebar.tree.set_counts(total, self._model.favorite_count(), total - videos, videos)

    def _update_header(self) -> None:
        if self._settings_mode:
            self._title_holder.show()
            self._title_icon.setPixmap(themed_pixmap("settings", "icon", 18))
            self._title.setText("Settings")
            # No subtitle under it, so centre the title instead of leaving it bottom-aligned (it sat too low).
            self._title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._subtitle.hide()
            self._sync_title_margins()
            return
        self._title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self._subtitle.show()
        scope = self._model.scope()
        if scope.kind == "photos":
            scope_title, icon_name = "Photos", "camera"
        elif scope.kind == "videos":
            scope_title, icon_name = "Videos", "video"
        elif scope.kind == "favorites":
            scope_title, icon_name = "Favourites", "heart"
        elif scope.kind == "album":
            scope_title, icon_name = scope.name, "album"
        elif scope.kind == "pc":
            scope_title, icon_name = "This PC", "computer"
        elif scope.kind == "folder" and scope.path is not None:
            scope_title, icon_name = scope.path.name or str(scope.path), "folder"
        elif scope.kind == "date":
            scope_title, icon_name = date_label(scope.year, scope.month), "calendar"
        elif scope.kind == "place" and scope.cell is not None:
            scope_title, icon_name = place_label(scope.cell), "map-pin"
        else:
            scope_title, icon_name = "Gallery", "gallery"

        total = self._model.rowCount()
        selected = len(self._view.selected_rows())
        if selected:
            self._title_holder.hide()
            self._title.setText(f"{selected} item{'s' if selected != 1 else ''} selected")
            subtitle = scope_title
        elif self._view.is_selecting():
            self._title_holder.hide()
            self._title.setText("Select items")
            subtitle = scope_title
        else:
            self._title_holder.show()
            self._title_icon.setPixmap(themed_pixmap(icon_name, "icon", 18))
            self._title.setText(scope_title)
            if self._scanning:
                subtitle = f"Loading... {total} found" if total else "Loading..."
            else:
                videos = self._model.video_count()
                photos = total - videos
                if not videos:
                    subtitle = f"{total} photo{'s' if total != 1 else ''}"
                elif not photos:
                    subtitle = f"{total} video{'s' if total != 1 else ''}"
                else:
                    subtitle = f"{photos} photo{'s' if photos != 1 else ''}, {videos} video{'s' if videos != 1 else ''}"
                packed = self._model.npi_count()
                if packed:
                    subtitle += f", {packed} in NPI"
        self._subtitle.setText(subtitle)
        self._sync_title_margins()
        self._view.set_empty_text(self._empty_message())

    def _empty_message(self) -> str:
        if self._scanning:
            return ""
        if not self._library:
            return "Your gallery is empty.\nUse Import to add a folder with photos."
        if self._model.scope().kind == "favorites" and not self._model.favorites():
            return "No favourites yet.\nHover over a photo and click the heart."
        if self._model.rowCount():
            return ""
        if self._model.scope().kind == "album":
            return "This album is empty.\nSelect photos, then use Add to album."
        return "No videos here" if self._model.scope().kind == "videos" else "No photos here"

    # actions

    def _set_row_height(self, height: int) -> None:
        self._view.set_row_height(height)
        self._remember_row_height(height)

    def _set_square(self, square: bool) -> None:
        self._view.set_square(square)
        self._settings.setValue("tile_square", square)

    def _remember_row_height(self, height: int) -> None:
        self._settings.setValue("row_height", height)

    def _remember_sidebar_width(self, width: int) -> None:
        self._settings.setValue("sidebar_width", width)

    def _open_batch(self, mode: BatchMode) -> None:
        scope = self._model.scope()
        start = scope.path if scope.kind == "folder" and scope.path else (self._library[0] if self._library else None)
        dialog = BatchDialog(mode, start, self)
        dialog.exec()
        changed = dialog.changed
        dialog.deleteLater()
        if changed:
            self.reload()

    def _selected_items(self) -> list:
        return [self._model.item_at(row) for row in self._view.selected_rows()]

    def _active_items(self) -> list:
        selected = self._selected_items()
        if selected:
            return selected
        row = self._view.current_row()
        if row is not None and 0 <= row < self._model.rowCount():
            return [self._model.item_at(row)]
        return []

    def _active_row(self) -> int | None:
        selected = self._view.selected_rows()
        if selected:
            return selected[0]
        return self._view.current_row()

    def _clear_cache(self) -> None:
        self._cache.clear()
        self._model.clear_pixmaps()
        self._refresh_cache_usage()

    def _refresh_cache_usage(self) -> None:
        try:
            count, size = self._cache.usage()
        except Exception:
            return  # the figure is a courtesy; never let it get in the way of opening Settings
        self._settings_page.set_cache_usage(count, size)

    # // window size memory
    # The un-maximized rectangle is tracked here instead of trusting QWidget.saveGeometry() /
    # normalGeometry(): on Windows Qt can report the maximized rectangle as the "normal" one, which
    # made Restore (or a relaunch) come back screen-sized.

    def _in_fullscreen(self) -> bool:
        """True only for the F11 full screen this window asked for.

        QWidget.isFullScreen() cannot be trusted on Windows: Qt flags any window whose geometry equals
        the screen's as full screen, and a maximized window does exactly that when the taskbar is set to
        auto-hide (no work-area strip is reserved). That made a maximized window look full screen, so
        it was never saved as maximized and was skipped when restoring.
        """
        return self._f11_active and QWidget.isFullScreen(self)

    def _is_zoomed(self) -> bool:
        if IS_WINDOWS and self.isVisible():
            try:
                return bool(ctypes.windll.user32.IsZoomed(int(self.winId())))
            except Exception:
                pass
        return self.isMaximized()

    def _fills_screen(self, rect: QRect) -> bool:
        # With an auto-hide taskbar availableGeometry() is the whole monitor, while Windows leaves a
        # few pixels of the maximized window out (frame insets, the strip kept for the taskbar), so an
        # exact ">=" never matched and a maximize in flight was stored as the "normal" size.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        available = screen.availableGeometry().united(screen.geometry())
        slack = max(16, round(16 * self.devicePixelRatioF()))
        return rect.width() >= available.width() - slack and rect.height() >= available.height() - slack

    def _track_normal_rect(self) -> None:
        if self._restoring or self._want_maximized:
            return
        if not self.isVisible() or self.isMinimized() or self._in_fullscreen() or self._is_zoomed():
            return
        rect = self.geometry()
        # A rectangle as big as the screen is the maximize animation in flight, not a size the user chose.
        if rect.isValid() and not self._fills_screen(rect):
            self._normal_rect = QRect(rect)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._track_normal_rect()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._track_normal_rect()
        if hasattr(self, "_grips"):  # restoreGeometry() moves the window during __init__, before the widgets exist
            backdrop().window_moved()  # Mica lines up with the desktop behind the window

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_grips"):
            zoomed = self._is_zoomed()
            left_maximized = self._prev_zoomed and not zoomed
            self._prev_zoomed = zoomed
            # Only a real maximized -> normal transition may move the window. Enforcing on every state
            # change also fired during the startup maximize, and setGeometry() there un-maximized it.
            if left_maximized and not self._restoring:
                QTimer.singleShot(0, self._enforce_normal_rect)
            QTimer.singleShot(150, self._remember_maximized)

    def _enforce_normal_rect(self) -> None:
        """After leaving maximized, put the window back at the size the user had, never at screen size."""
        if self._restoring or self._normal_rect is None or self.isMinimized() or self._in_fullscreen() or self._is_zoomed():
            return
        if self._fills_screen(self.geometry()):
            self.setGeometry(self._fit_to_screens(self._normal_rect))

    def _fit_to_screens(self, rect: QRect) -> QRect:
        """Keep a remembered rectangle on a screen that exists now (a monitor may have been unplugged)."""
        screens = QGuiApplication.screens()
        if not screens:
            return rect
        best, overlap = screens[0], 0
        for screen in screens:
            common = rect.intersected(screen.availableGeometry())
            area = max(0, common.width()) * max(0, common.height())
            if area > overlap:
                best, overlap = screen, area
        available = best.availableGeometry()
        width = min(max(rect.width(), self.minimumWidth()), available.width())
        height = min(max(rect.height(), self.minimumHeight()), available.height())
        if overlap == 0:  # its screen is gone: centre on the primary one
            available = (QGuiApplication.primaryScreen() or best).availableGeometry()
            x = available.x() + (available.width() - width) // 2
            y = available.y() + (available.height() - height) // 2
        else:
            x = min(max(rect.x(), available.left()), available.right() - width + 1)
            y = min(max(rect.y(), available.top()), available.bottom() - height + 1)
        return QRect(x, y, width, height)

    def _restore_window_state(self) -> None:
        rect = self._settings.value("window/normal_rect")
        if isinstance(rect, QRect) and rect.isValid() and rect.width() > 0 and rect.height() > 0:
            self._normal_rect = QRect(rect)
            self.setGeometry(self._fit_to_screens(rect))
        if self._settings.value("window/maximized", False, bool):
            self._want_maximized = True
            self._restoring = True
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        backdrop().sync_window()  # the native window exists now: switch the system Acrylic on if that is the background
        if self._want_maximized:
            self._want_maximized = False
            QTimer.singleShot(0, self._ensure_maximized)

    def _ensure_maximized(self) -> None:
        if self.isMinimized() or self._in_fullscreen():
            self._restoring = False
            return
        if self._is_zoomed():
            self._seen_zoomed = True
            self._restoring = False
            return
        if IS_WINDOWS:
            ctypes.windll.user32.ShowWindow(int(self.winId()), SW_MAXIMIZE)
        else:
            self.showMaximized()
        QTimer.singleShot(250, self._finish_restore)

    def _finish_restore(self) -> None:
        if self.isMinimized() or self._in_fullscreen():
            self._restoring = False
            return
        if self._is_zoomed():
            self._seen_zoomed = True
            self._restoring = False
            return
        # Not maximized yet (Windows can swallow the first request while the window is still being
        # shown, notably with an auto-hide taskbar): ask again a few times before giving up. Giving up
        # leaves the saved "maximized" preference alone, see _remember_maximized().
        self._restore_attempts += 1
        if self._restore_attempts <= 4:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
            self.showMaximized()
            if IS_WINDOWS:
                ctypes.windll.user32.ShowWindow(int(self.winId()), SW_MAXIMIZE)
            QTimer.singleShot(250, self._finish_restore)
            return
        self._restoring = False

    def _maximized_to_save(self) -> bool | None:
        """What to persist, or None to keep the stored value.

        A failed or still-pending startup restore must never overwrite the saved preference with False;
        that turned one missed maximize into a permanent windowed start.
        """
        if self._in_fullscreen():
            return self._fullscreen_from_maximized
        if self._is_zoomed() or (self.isMaximized() and self._seen_zoomed):
            self._seen_zoomed = True
            return True
        if self._seen_zoomed or not self._settings.value("window/maximized", False, bool):
            return False  # the user really did restore the window (or it was never maximized)
        return None

    def _remember_maximized(self) -> None:
        if self._restoring or not self.isVisible() or self.isMinimized() or self._in_fullscreen():
            return
        value = self._maximized_to_save()
        if value is not None:
            self._settings.setValue("window/maximized", value)
            self._settings.sync()

    def toggle_fullscreen(self) -> None:
        """F11. Leaving full screen goes back to where it came from: maximized, or the remembered size."""
        if self._in_fullscreen():
            self._f11_active = False
            if self._fullscreen_from_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._fullscreen_from_maximized = self._is_zoomed()
            self._f11_active = True
            self.showFullScreen()

    def _save_window_state(self) -> None:
        self._track_normal_rect()
        if self._normal_rect is not None:
            self._settings.setValue("window/normal_rect", self._normal_rect)
        # Closed in full screen: relaunch as what it was before (never start in full screen).
        value = self._maximized_to_save()
        if value is not None:
            self._settings.setValue("window/maximized", value)
        self._settings.remove("geometry")  # the old saveGeometry() blob that caused the wrong size
        self._settings.sync()

    def closeEvent(self, event) -> None:
        for viewer in list(self._viewers):
            viewer.close()
        self._save_window_state()
        self._stop_scan()
        self._stop_duplicates()
        self._loader.shutdown()
        self._cache.close()
        event.accept()