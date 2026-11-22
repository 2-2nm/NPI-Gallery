from PySide6.QtCore import Qt

from ..common.motion import DURATION_NORMAL, DURATION_SLOW


KIND_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
ROOT_ROLE = Qt.ItemDataRole.UserRole + 2
COUNT_ROLE = Qt.ItemDataRole.UserRole + 3
SCOPE_ROLE = Qt.ItemDataRole.UserRole + 4
SCOPE_KINDS = ("gallery", "photos", "videos", "favorites", "group", "folder", "year", "month", "place", "album")
EXPANDABLE_KINDS = ("folder", "year")
INDENTED_KINDS = ("folder", "year", "month", "place", "album")
COUNTED_KINDS = ("year", "month", "place", "album")
DEFAULT_EXPANDED_WIDTH = 224
MIN_EXPANDED_WIDTH = 176
MAX_EXPANDED_WIDTH = 520
COMPACT_WIDTH = 72
TITLE_MIN_WIDTH = 190  # the "NPI Gallery" title needs this much room next to the logo
HANDLE_WIDTH = 6
CARD_MARGIN_LEFT = 12
CARD_MARGIN_TOP = 12
CARD_MARGIN_RIGHT = 4
CARD_MARGIN_BOTTOM = 12
TREE_PADDING = 8
NAV_ICON_X = 13  # icon x of the top-level rows, relative to the row's own indent
FOLDER_ICON_X = 8  # folders sit left of the indent because the chevron lives in front of the icon
CHEVRON_GAP = 18
ROW_HEIGHT = 36
SEPARATOR_HEIGHT = 10
ROW_RADIUS = 12.0
INDENT = 16
ACTION_ZONE = 36
REVEAL_DURATION = DURATION_NORMAL + 40
HIGHLIGHT_DURATION = DURATION_SLOW - 60
SLIDE_MAX = ROW_HEIGHT * 4  # farthest the rows of an opening folder travel while sliding in
BIG_FOLDER_ROWS = 8  # above this many visible rows the motion gets longer and gentler
SIDEBAR_DURATION = DURATION_NORMAL + 40