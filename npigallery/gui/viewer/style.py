"""Fixed visual constants of the immersive viewer. The viewer is always dark (it is a photo
stage), so it does not follow the light/dark app theme."""

from PySide6.QtGui import QColor

from ..theme import accent, theme

CANVAS = QColor(10, 10, 10)
CAPTION = "#0a0a0a"

GLASS = QColor(30, 30, 30, 206)
GLASS_BORDER = QColor(255, 255, 255, 30)
CARD = QColor(26, 26, 26, 240)
HOVER = QColor(255, 255, 255, 30)
PRESSED = QColor(255, 255, 255, 18)
SOFT = QColor(255, 255, 255, 22)
TEXT = QColor(255, 255, 255)
TEXT_DIM = QColor(255, 255, 255, 150)
ICON = QColor(242, 242, 242)
ACCENT = QColor("#7fb2ff")
ON_ACCENT = QColor("#10151f")
HEART = QColor("#ff6b81")


def floating(color: QColor) -> QColor:
    """`color` with the "Floating panel opacity" setting applied on top of its own alpha (read at paint time)."""
    result = QColor(color)
    result.setAlphaF(result.alphaF() * theme.float_opacity())
    return result


def accent_pair() -> tuple[QColor, QColor]:
    chosen = accent.pair(CARD.name(), True)
    if chosen is None:
        return QColor(ACCENT), QColor(ON_ACCENT)
    return QColor(chosen[0]), QColor(chosen[1])

MARGIN = 16
GAP = 8
TOP_HEIGHT = 46
BOTTOM_HEIGHT = 40
STRIP_HEIGHT = 58
IDLE_MS = 2600
MIN_WINDOW = (640, 420)