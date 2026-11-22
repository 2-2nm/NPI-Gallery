from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ...config import APP_TITLE
from ..common.branding import logo_pixmap
from ..common.icons import icon
from ..common.motion import FrameAnimation, decelerate
from ..theme import theme
from ..theme.preferences import prefs
from .background import DEFAULT_CROP, BackgroundRenderer, background_pixmap, cached_image, crop_region, parse_crop
from .metrics import (
    CARD_MARGIN_BOTTOM,
    CARD_MARGIN_LEFT,
    CARD_MARGIN_RIGHT,
    CARD_MARGIN_TOP,
    COMPACT_WIDTH,
    DEFAULT_EXPANDED_WIDTH,
    HANDLE_WIDTH,
    MAX_EXPANDED_WIDTH,
    MIN_EXPANDED_WIDTH,
    SIDEBAR_DURATION,
    TITLE_MIN_WIDTH,
    TREE_PADDING,
)
from .resize_handle import SidebarResizeHandle
from .tree import NavTree


class SidebarCard(QFrame):
    """The floating sidebar panel; optionally shows a dimmed picture behind its contents."""

    RADIUS = 22  # keep in step with QFrame#SidebarCard in theme.py

    def __init__(self, parent=None):
        super().__init__(parent)
        self._picture: QPixmap | None = None
        self._dim = 0.6
        self._crop = DEFAULT_CROP
        self._framed: QPixmap | None = None
        self._framed_key: tuple | None = None

    def set_picture(self, picture: QPixmap | None, dim: float, crop=DEFAULT_CROP) -> None:
        self._picture = picture
        self._dim = max(0.0, min(1.0, dim))
        self._crop = crop
        self._framed = None
        self._framed_key = None
        self.update()

    def set_dim(self, dim: float) -> None:
        dim = max(0.0, min(1.0, dim))
        if dim == self._dim:
            return
        self._dim = dim
        self.update()

    def _framed_picture(self, picture: QPixmap, inner: QRectF) -> QPixmap:
        ratio = self.devicePixelRatioF()
        size = (max(1, round(inner.width() * ratio)), max(1, round(inner.height() * ratio)))
        key = (picture.cacheKey(), self._crop, size, ratio)
        if self._framed is not None and self._framed_key == key:
            return self._framed
        x, y, width, height = crop_region(picture.width(), picture.height(), size[0] / size[1], *self._crop)
        framed = QPixmap(size[0], size[1])
        painter = QPainter(framed)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(QRectF(0, 0, size[0], size[1]), picture, QRectF(x, y, width, height))
        painter.end()
        framed.setDevicePixelRatio(ratio)
        self._framed, self._framed_key = framed, key
        return framed

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the themed fill and border
        picture = self._picture
        if picture is None or picture.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        inner = QRectF(self.rect()).adjusted(1, 1, -1, -1)  # inside the 1 px border
        clip = QPainterPath()
        clip.addRoundedRect(inner, self.RADIUS - 1, self.RADIUS - 1)
        painter.setClipPath(clip)
        # Show the chosen part of the picture, stretched over the whole panel. The part is stored as
        # centre + zoom, so it follows the panel when the sidebar is dragged to another width.
        painter.drawPixmap(inner.topLeft(), self._framed_picture(picture, inner))
        # The sidebar's own color over it keeps text readable in both themes; a bit denser at the top,
        # where the logo and navigation sit, so the picture shows more towards the bottom.
        tone = QColor(theme.palette().float_bg)
        gradient = QLinearGradient(inner.topLeft(), inner.bottomLeft())
        for position, alpha in ((0.0, min(1.0, self._dim + 0.2)), (1.0, self._dim)):
            color = QColor(tone)
            color.setAlphaF(alpha)
            gradient.setColorAt(position, color)
        painter.fillRect(inner, gradient)
        painter.end()


class Sidebar(QWidget):
    width_changed = Signal(int)
    width_animation_started = Signal(int, int)  # start width, end width

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._expanded_width = DEFAULT_EXPANDED_WIDTH
        self._compact = False
        self._compact_visuals = False
        self._width_animation: FrameAnimation | None = None
        self.setFixedWidth(self._expanded_width)

        self.card = SidebarCard(self)
        self.card.setObjectName("SidebarCard")
        self.card.setProperty("dragRegion", True)

        # The hamburger button (as in Windows Photos) sits on its own row under the logo, in line with
        # the navigation icons below it, and is what collapses / expands the navigation.
        # The logo and title are branding only; clicking them does nothing.
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("SidebarMenu")
        self.menu_button.setIcon(icon("menu"))
        self.menu_button.setIconSize(QSize(18, 18))
        self.menu_button.setFixedSize(QSize(34, 34))
        self.menu_button.setToolTip("Navigation")
        self.menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_button.clicked.connect(self.toggle_compact)
        self.logo_label = QLabel()
        self.logo_label.setObjectName("SidebarLogo")
        self.logo_label.setPixmap(logo_pixmap(26))
        self.logo_label.setFixedSize(QSize(34, 34))
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label = QLabel(APP_TITLE)
        self.title_label.setObjectName("SidebarTitle")

        self.tree = NavTree(self)

        self._header = QHBoxLayout()
        self._header.setSpacing(8)
        self._header.addWidget(self.logo_label)
        self._header.addWidget(self.title_label)
        self._header.addStretch(1)

        self._menu_row = QHBoxLayout()
        self._menu_row.addWidget(self.menu_button)
        self._menu_row.addStretch(1)

        tree_row = QHBoxLayout()
        tree_row.setContentsMargins(TREE_PADDING, 0, TREE_PADDING, 12)
        tree_row.addWidget(self.tree)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        card_layout.addLayout(self._header)
        card_layout.addLayout(self._menu_row)
        card_layout.addSpacing(4)
        card_layout.addLayout(tree_row, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_LEFT, CARD_MARGIN_TOP, CARD_MARGIN_RIGHT, CARD_MARGIN_BOTTOM)
        layout.setSpacing(0)
        layout.addWidget(self.card)
        self._set_compact_visuals(False)

        self._handle = SidebarResizeHandle(self)
        self._position_handle()
        self._renderer = BackgroundRenderer(self)
        self._renderer.rendered.connect(self._on_rendered, Qt.ConnectionType.QueuedConnection)
        self.refresh_background()
        prefs().changed.connect(self._on_preference_changed)

    def _on_preference_changed(self, key: str) -> None:
        if key == "sidebar_bg_dim":
            self.card.set_dim(prefs().get(key) / 100)
        elif key == "sidebar_bg_blur":
            self._refresh_blur()
        elif key.startswith("sidebar_bg_"):
            self.refresh_background()

    def _refresh_blur(self) -> None:
        store = prefs()
        if store.get("sidebar_bg_mode") != "picture":
            return
        path, blur = store.get("sidebar_bg_path"), store.get("sidebar_bg_blur")
        image = cached_image(path, blur)
        if image is not None:
            self._show_image(image)
        else:
            self._renderer.request(path, blur)

    def _on_rendered(self, path: str, blur: int, image: QImage) -> None:
        store = prefs()
        if store.get("sidebar_bg_mode") == "picture" and (path, blur) == (
            store.get("sidebar_bg_path"),
            store.get("sidebar_bg_blur"),
        ):
            self._show_image(image)

    def _show_image(self, image: QImage) -> None:
        store = prefs()
        self.card.set_picture(
            QPixmap.fromImage(image), store.get("sidebar_bg_dim") / 100, parse_crop(store.get("sidebar_bg_crop"))
        )

    def refresh_background(self) -> None:
        store = prefs()
        picture = None
        if store.get("sidebar_bg_mode") == "picture":
            picture = background_pixmap(store.get("sidebar_bg_path"), store.get("sidebar_bg_blur"))
        self.card.set_picture(picture, store.get("sidebar_bg_dim") / 100, parse_crop(store.get("sidebar_bg_crop")))
        self._sync_float_tint()

    def _sync_float_tint(self) -> None:
        """Without a sidebar picture the panel is a plain floating plate and follows the opacity setting."""
        tint = prefs().get("sidebar_bg_mode") != "picture"
        if self.card.property("floatTint") != tint:
            self.card.setProperty("floatTint", tint)
            self.card.style().unpolish(self.card)
            self.card.style().polish(self.card)

    def background_size(self) -> QSize:
        """Size of the panel the picture is shown in; the collapsed rail counts as the expanded panel."""
        width = self._expanded_width - CARD_MARGIN_LEFT - CARD_MARGIN_RIGHT if self._compact else self.card.width()
        return QSize(max(1, width), max(1, self.card.height()))

    def _sync_brand(self) -> None:
        """The title is hidden in the compact rail, and also gives way when the panel is too narrow
        to hold it next to the logo."""
        self.title_label.setVisible(not self._compact_visuals and self.width() >= TITLE_MIN_WIDTH)

    def _set_compact_visuals(self, compact: bool) -> None:
        self._compact_visuals = compact
        self._sync_brand()
        inner = COMPACT_WIDTH - CARD_MARGIN_LEFT - CARD_MARGIN_RIGHT - 2
        side = (inner - 34) // 2 if compact else 12
        self._header.setContentsMargins(side, 14, 0 if compact else 12, 2)
        self._menu_row.setContentsMargins(side, 0, 0 if compact else 12, 0)

    def expanded_width(self) -> int:
        return self._expanded_width

    def set_expanded_width(self, width: int, persist: bool = False) -> None:
        clamped = max(MIN_EXPANDED_WIDTH, min(MAX_EXPANDED_WIDTH, int(width)))
        self._expanded_width = clamped
        if self._compact:
            self._compact = False
            self.tree.actions_visible = True
            self.tree.set_compact(False)
            self._set_compact_visuals(False)
        self.setFixedWidth(clamped)
        self.tree.viewport().update()
        if persist:
            self.width_changed.emit(clamped)

    def _apply_drag_width(self, raw_width: int) -> None:
        if self._width_animation is not None:
            self._width_animation.stop()
        # Allow dragging out of compact mode once stretched past 115 px, or collapsing
        # back into compact mode if dragged below 105 px.
        if raw_width < 105:
            if not self._compact:
                self._compact = True
                self.tree.actions_visible = False
                self.tree.set_compact(True)
                self._set_compact_visuals(True)
            self.setFixedWidth(COMPACT_WIDTH)
            return

        target = max(MIN_EXPANDED_WIDTH, min(MAX_EXPANDED_WIDTH, raw_width))
        if self._compact:
            self._compact = False
            self.tree.actions_visible = True
            self.tree.set_compact(False)
            self._set_compact_visuals(False)
        self._expanded_width = target
        self.setFixedWidth(target)
        self.tree.viewport().update()

    def _finish_drag(self) -> None:
        if not self._compact:
            self.width_changed.emit(self._expanded_width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_handle()
        self._sync_brand()

    def _position_handle(self) -> None:
        self._handle.setGeometry(self.width() - HANDLE_WIDTH, 0, HANDLE_WIDTH, self.height())
        self._handle.raise_()

    def toggle_compact(self) -> None:
        self._compact = not self._compact
        if self._compact:
            # Labels, chevrons and "+" disappear immediately; only the width animates.
            self.tree.actions_visible = False
            self.tree.set_compact(True)
            self._set_compact_visuals(True)
        else:
            self.tree.actions_visible = False  # shown again once the panel is wide
            self.tree.set_compact(False)

        if self._width_animation is not None:
            self._width_animation.stop()
            self._width_animation.deleteLater()
        start = self.width()
        end = COMPACT_WIDTH if self._compact else self._expanded_width
        animation = FrameAnimation(self, SIDEBAR_DURATION, decelerate())
        animation.valueChanged.connect(lambda value: self.setFixedWidth(round(start + (end - start) * value)))

        def finished() -> None:
            self.setFixedWidth(end)
            self.tree.actions_visible = not self._compact
            if not self._compact:
                self._set_compact_visuals(False)
            self.tree.viewport().update()

        animation.finished.connect(finished)
        self._width_animation = animation
        # Lets the photo area lay out once for its final width and glide there.
        self.width_animation_started.emit(start, end)
        animation.start()