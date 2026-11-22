from PySide6.QtCore import QModelIndex, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QTreeWidgetItem, QWidget

from ..common.motion import FrameAnimation, decelerate, standard
from .metrics import BIG_FOLDER_ROWS, KIND_ROLE, PATH_ROLE, REVEAL_DURATION, ROW_HEIGHT, ROW_RADIUS, SLIDE_MAX
from .nav_delegate import tone


class NavMotionMixin:
    def reveal_scale(self, index: QModelIndex) -> float:
        if not self._reveal:
            return 1.0
        item = self.itemFromIndex(index)
        return self._reveal.get(self._key(item), 1.0) if item is not None else 1.0

    def is_collapsing(self, index: QModelIndex) -> bool:
        if not self._collapsing:
            return False
        item = self.itemFromIndex(index)
        return item is not None and self._key(item) in self._collapsing

    @staticmethod
    def _key(item: QTreeWidgetItem) -> str:
        """Stable identity for a row (folders are unique by path; group/gallery rows by kind)."""
        path = item.data(0, PATH_ROLE)
        return str(path) if path is not None else str(item.data(0, KIND_ROLE))

    def _motion_blocked(self) -> bool:
        return self._suppress_motion > 0 or self.compact or self.signalsBlocked() or not self.isVisible()

    @staticmethod
    def _descendants(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        rows: list[QTreeWidgetItem] = []
        for position in range(item.childCount()):
            child = item.child(position)
            rows.append(child)
            if child.isExpanded():
                rows.extend(NavMotionMixin._descendants(child))
        return rows

    def toggle_item(self, item: QTreeWidgetItem) -> None:
        """Expand or collapse with motion (a plain setExpanded would jump)."""
        if not item.isExpanded():
            if not self._motion_blocked():
                self._finish_snap()
                self.doItemsLayout()
                # Picture of the list as it is now: the rows below this folder, before they get pushed down.
                self._pre_expand = (self._key(item), self._render_viewport())
            item.setExpanded(True)  # itemExpanded starts the animation
            return
        items = [] if self._motion_blocked() or self._key(item) in self._collapsing else self._descendants(item)
        if not items:
            self._finish_snap()
            item.setExpanded(False)
            return
        self._begin_collapse(item, items)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        pre, self._pre_expand = self._pre_expand, None
        if self._motion_blocked() or pre is None or pre[0] != self._key(item):
            return
        items = self._descendants(item)
        if not items:
            return
        self.doItemsLayout()
        span = self._span(item, items)
        if span is not None:
            self._start_snap("open", span[0], span[1], self._render_viewport(), pre[1])

    def _begin_collapse(self, item: QTreeWidgetItem, items: list[QTreeWidgetItem]) -> None:
        self._finish_snap()
        self.doItemsLayout()
        span = self._span(item, items)
        if span is None:
            item.setExpanded(False)
            return
        self._collapsing.add(self._key(item))  # chevron turns right straight away
        expanded = self._render_viewport()
        self._suppress_motion += 1
        try:
            item.setExpanded(False)  # the real tree is already in its final state; the animation is a picture
        finally:
            self._suppress_motion -= 1
        self.doItemsLayout()
        self._start_snap("close", span[0], span[1], expanded, self._render_viewport(), owner=item)

    def _span(self, owner: QTreeWidgetItem, items: list[QTreeWidgetItem]) -> tuple[int, int] | None:
        """(y where the folder's rows start, their total height) in the expanded layout."""
        top = self.visualItemRect(owner)
        if top.isEmpty():
            return None
        # Every row under a folder is a plain folder row, so no need to ask Qt where the last (possibly
        # thousands of pixels away) one is.
        return top.bottom() + 1, len(items) * ROW_HEIGHT

    def _render_viewport(self) -> QPixmap:
        """What the list looks like right now, on a transparent background and without the sliding highlight."""
        viewport = self.viewport()
        ratio = viewport.devicePixelRatioF()
        pixmap = QPixmap(max(1, round(viewport.width() * ratio)), max(1, round(viewport.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        self._no_highlight = True
        try:
            viewport.render(pixmap, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        finally:
            self._no_highlight = False
        return pixmap

    def _start_snap(self, mode: str, y0: int, height: int, expanded: QPixmap, rest: QPixmap, owner=None) -> None:
        """Animate a folder opening/closing by moving pictures around.

        Re-laying out and repainting every row each frame (Python delegate, SVG icons) cost more
        the more subfolders there were, hence the stutter. Here each frame is three pixmap blits:
        the rows above the folder, its own rows growing/shrinking (`expanded` picture), and
        everything below it sliding (`rest`: the list without the folder's rows).
        """
        opening = mode == "open"
        # A folder with hundreds of subfolders would have to fly thousands of pixels in 200 ms ("thrown").
        # Only what fits on screen is ever visible, so animate that much, move the content a bounded
        # distance, and give tall reveals more time and a gentler curve.
        height = min(height, max(ROW_HEIGHT, self.viewport().height() - y0))
        rows = height / ROW_HEIGHT
        duration = REVEAL_DURATION + int(min(180, max(0.0, rows - 6) * 14))
        curve = decelerate() if rows <= BIG_FOLDER_ROWS else standard()
        slide = min(1.0, SLIDE_MAX / height)
        self._animating.set()  # scan threads stand still while this plays
        self._snap = {"opening": opening, "y0": y0, "h": height, "e": expanded, "r": rest, "q": 0.0 if opening else 1.0, "k": slide, "size": self.viewport().size()}
        animation = FrameAnimation(self, duration, curve, 0.0 if opening else 1.0, 1.0 if opening else 0.0)
        animation.valueChanged.connect(self._on_snap_step)
        animation.finished.connect(self._finish_snap)
        self._snap_animation = animation
        self.viewport().update()
        animation.start()

    def _on_snap_step(self, value) -> None:
        if self._snap is not None:
            self._snap["q"] = max(0.0, min(1.0, float(value)))
            self.viewport().update()

    def _finish_snap(self) -> None:
        animation, self._snap_animation = self._snap_animation, None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self._animating.clear()
        if self._snap is None and not self._collapsing:
            return
        self._snap = None
        self._collapsing.clear()
        self.viewport().update()

    def _paint_snapshot(self) -> None:
        snap = self._snap
        viewport = self.viewport()
        width, full = viewport.width(), viewport.height()
        y0, height, q = snap["y0"], snap["h"], snap["q"]
        grown = height * q
        painter = QPainter(viewport)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Selection highlight, live (it may be sliding). Rows above the folder stay put; the rest
        # sits in the real layout, which is the expanded one when opening and the closed one when closing.
        shift = grown - height if snap["opening"] else grown
        # Draw it only in the part of the list it belongs to. Painting it in both made the folder's own
        # highlight (in the part above) also get pushed down into the moving part when closing.
        below = False
        current = self.currentItem()
        if current is not None:
            rect = self.visualItemRect(current)
            if not rect.isEmpty():
                lead = float(rect.top())
                if self._hl_from is not None:
                    lead = self._hl_from + (lead - self._hl_from) * self._hl_progress
                below = lead + ROW_HEIGHT / 2 > y0
        painter.save()
        if below:
            painter.setClipRect(QRect(0, y0, width, max(0, full - y0)))
            painter.translate(0, shift)
        else:
            painter.setClipRect(QRect(0, 0, width, y0))
        self._paint_highlight(painter)
        painter.restore()

        # Rows above the folder.
        painter.save()
        painter.setClipRect(QRect(0, 0, width, y0))
        painter.drawPixmap(0, 0, snap["e"])
        painter.restore()
        # The folder's own rows, sliding out from under the folder row and fading with it.
        strip = round(grown)
        if strip > 0:
            painter.save()
            painter.setClipRect(QRect(0, y0, width, strip))
            painter.setOpacity(q)
            painter.drawPixmap(0, round((grown - height) * snap["k"]), snap["e"])
            painter.restore()
        # Everything below, pushed down by what has grown so far.
        painter.save()
        painter.setClipRect(QRect(0, y0 + strip, width, max(0, full - y0 - strip)))
        painter.drawPixmap(0, strip, snap["r"])
        painter.restore()
        painter.end()

    def _start_highlight(self, previous: QTreeWidgetItem | None, current: QTreeWidgetItem | None) -> None:
        self._hl_animation.stop()
        self._hl_from = None
        self._hl_progress = 1.0
        if previous is None or current is None or self._motion_blocked():
            return
        before = self.visualItemRect(previous)
        after = self.visualItemRect(current)
        if before.isEmpty() or after.isEmpty() or before.top() == after.top():
            return
        self._hl_from = float(before.top())
        self._hl_progress = 0.0
        self._hl_animation.start()

    def _on_highlight_step(self, value) -> None:
        self._hl_progress = max(0.0, min(1.0, float(value)))
        self.viewport().update()

    def _on_highlight_done(self) -> None:
        self._hl_from = None
        self._hl_progress = 1.0
        self.viewport().update()

# in the river

    def set_highlight_hidden(self, hidden: bool) -> None:
        """Hide the selection highlight while a page that is not a photo scope (Settings) is open."""
        if self._highlight_hidden == hidden:
            return
        self._highlight_hidden = hidden
        self._hl_animation.stop()
        self._hl_from = None
        self._hl_progress = 1.0
        self.viewport().update()

    def _paint_highlight(self, painter: QPainter) -> None:
        item = self.currentItem()
        if self._no_highlight or self._highlight_hidden or item is None or not item.isSelected():
            return
        if item.data(0, KIND_ROLE) in ("separator", "placeholder"):
            return
        rect = self.visualItemRect(item)
        if rect.isEmpty():
            return
        width = self.viewport().width()
        target = float(rect.top())
        height = float(rect.height())
        source = self._hl_from
        progress = self._hl_progress
        lead_top = target if source is None else source + (target - source) * progress
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tone("nav_active_bg"))
        painter.drawRoundedRect(QRectF(0, lead_top + 1, width, height - 2), ROW_RADIUS, ROW_RADIUS)

    @property
    def highlight_visible(self) -> bool:
        return not self._highlight_hidden
