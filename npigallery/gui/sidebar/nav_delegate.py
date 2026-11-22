from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QStyledItemDelegate

from ..common.icons import draw_icon
from ..theme import theme
from .metrics import (
    CHEVRON_GAP,
    COUNT_ROLE,
    COUNTED_KINDS,
    EXPANDABLE_KINDS,
    FOLDER_ICON_X,
    INDENTED_KINDS,
    KIND_ROLE,
    NAV_ICON_X,
    PATH_ROLE,
    ROW_HEIGHT,
    ROW_RADIUS,
    SEPARATOR_HEIGHT,
)

if TYPE_CHECKING:
    from .tree import NavTree


ICON_NAMES = {
    "gallery": "gallery",
    "photos": "camera",
    "videos": "video",
    "favorites": "heart",
    "group": "computer",
    "year": "calendar",
    "month": "calendar",
    "place": "map-pin",
    "album": "album",
}
SECTION_ICONS = {
    "section:folders": "folder",
    "section:albums": "album",
    "section:date": "calendar",
    "section:places": "map-pin",
}


def tone(role: str) -> QColor:
    """Color from the active theme, resolved at paint time so theme switches apply live."""
    return QColor(getattr(theme.palette(), role))


class NavDelegate(QStyledItemDelegate):
    def __init__(self, tree: "NavTree"):
        super().__init__(tree)
        self._tree = tree

    def sizeHint(self, option, index: QModelIndex) -> QSize:
        kind = index.data(KIND_ROLE)
        height = SEPARATOR_HEIGHT if kind == "separator" else ROW_HEIGHT
        scale = self._tree.reveal_scale(index)
        if scale < 1.0:
            height = max(0, round(height * scale))
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        scale = self._tree.reveal_scale(index)
        if scale >= 1.0:
            self._paint_row(painter, option, index)
            return
        if option.rect.height() <= 0:
            return
        # Rows growing in (or shrinking away) keep their full-size look: clip to the visible
        # strip, fade with it, and let the content slide out from under the row above.
        painter.save()
        painter.setClipRect(option.rect)
        painter.setOpacity(max(0.0, min(1.0, scale)))
        full = QStyleOptionViewItem(option)
        full.rect = QRect(
            option.rect.left(), option.rect.bottom() + 1 - ROW_HEIGHT, option.rect.width(), ROW_HEIGHT
        )
        self._paint_row(painter, full, index)
        painter.restore()

    def _paint_row(self, painter: QPainter, option, index: QModelIndex) -> None:
        kind = index.data(KIND_ROLE)
        width = self._tree.viewport().width()
        if kind == "separator":
            if self._tree.compact:
                y = option.rect.center().y()
                painter.fillRect(QRect(10, y, max(0, width - 20), 1), tone("separator"))
            return

        row = QRect(0, option.rect.top() + 1, width, option.rect.height() - 2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and self._tree.highlight_visible
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # The selected row's fill is painted by NavTree (it slides between rows).
        if hovered and not selected and kind != "group":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tone("sidebar_hover"))
            painter.drawRoundedRect(QRectF(row), ROW_RADIUS, ROW_RADIUS)
        self._paint_content(painter, option, index, row, kind, selected)
        painter.restore()

    def _paint_content(self, painter: QPainter, option, index, row: QRect, kind: str, selected: bool) -> None:
        compact = self._tree.compact
        left = option.rect.left()
        foreground = tone("nav_active_fg") if selected else tone("text")
        muted = tone("nav_active_fg") if selected else tone("text_muted")
        centre = row.center().y()

        if kind == "group" and not compact:
            font = QFont(painter.font())
            font.setPointSizeF(max(8.5, font.pointSizeF() - 0.5))
            font.setWeight(QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
            painter.setFont(font)
            painter.setPen(muted)
            label_rect = QRect(left + NAV_ICON_X + 1, row.top(), max(0, row.width() - 60), row.height())
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                str(index.data(Qt.ItemDataRole.DisplayRole)).upper(),
            )
            return

        if kind == "section":
            name = SECTION_ICONS.get(index.data(PATH_ROLE), "folder")
        else:
            name = ICON_NAMES.get(kind, "folder")
        if compact:
            icon_x = (self._tree.viewport().width() - 17) / 2
        elif kind in INDENTED_KINDS:
            icon_x = left + FOLDER_ICON_X
        else:
            icon_x = left + NAV_ICON_X
        draw_icon(painter, name, QRectF(icon_x, centre - 8.5, 17, 17), foreground, stroke=1.7)
        if compact:
            return

        if kind in EXPANDABLE_KINDS and self._tree.actions_visible and index.model().rowCount(index) > 0:
            expanded = self._tree.isExpanded(index) and not self._tree.is_collapsing(index)
            draw_icon(
                painter,
                "chevron-down" if expanded else "chevron-right",
                QRectF(icon_x - CHEVRON_GAP, centre - 6, 12, 12),
                muted,
                stroke=2.0,
            )

        font = QFont(painter.font())
        font.setPointSizeF(font.pointSizeF() + 1.0)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        right_limit = row.right() - 12
        count = self._tree.counts.get(kind) if kind in ("gallery", "photos", "videos", "favorites") else None
        if kind in COUNTED_KINDS:
            count = index.data(COUNT_ROLE)
        if kind == "section":
            right_limit = row.right() - (60 if index.data(PATH_ROLE) == "section:folders" else 36)
        if count is not None:
            count_font = QFont(font)
            count_font.setPointSizeF(font.pointSizeF() - 0.5)
            painter.setFont(count_font)
            text = f"{count:,}"
            count_width = painter.fontMetrics().horizontalAdvance(text)
            painter.setPen(muted)
            painter.drawText(row.right() - 14 - count_width, self._baseline(painter, centre), text)
            right_limit = row.right() - 14 - count_width - 8
            painter.setFont(font)

        painter.setPen(foreground)
        text_left = int(icon_x) + 27
        text_width = max(0, right_limit - text_left)
        painter.drawText(
            text_left,
            self._baseline(painter, centre),
            painter.fontMetrics().elidedText(
                index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideRight, text_width
            ),
        )
        if kind == "section":
            self._paint_section_tools(painter, index, row, centre, muted)

    @staticmethod
    def _baseline(painter: QPainter, centre: int) -> int:
        """Baseline that puts the middle of the capital letters on `centre` (the icon's centre line).

        Qt's AlignVCenter centres the whole line box (ascent + descent), which leaves the visible
        letters a couple of pixels below the icon next to them.
        """
        return round(centre + painter.fontMetrics().capHeight() / 2)

    def _paint_section_tools(self, painter: QPainter, index, row: QRect, centre: int, muted: QColor) -> None:
        if not self._tree.actions_visible:
            return
        if index.model().rowCount(index) > 0:
            expanded = self._tree.isExpanded(index) and not self._tree.is_collapsing(index)
            draw_icon(
                painter,
                "chevron-down" if expanded else "chevron-right",
                QRectF(row.right() - 28, centre - 6, 12, 12),
                muted,
                stroke=2.0,
            )
        if index.data(PATH_ROLE) == "section:folders":
            if self._tree.add_hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(tone("sidebar_hover"))
                painter.drawRoundedRect(QRectF(row.right() - 59, centre - 12, 24, 24), 8, 8)
            draw_icon(painter, "folder-plus", QRectF(row.right() - 55, centre - 8, 16, 16), tone("text"), stroke=1.7)