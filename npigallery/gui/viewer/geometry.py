"""Pure layout / formatting maths for the photo viewer."""

from datetime import datetime

# // filmstrip 
STRIP_THUMB_W = 60
STRIP_GAP = 6
STRIP_SLOT = STRIP_THUMB_W + STRIP_GAP
STRIP_PAD = 10


def strip_total(count: int, slot: int = STRIP_SLOT, pad: int = STRIP_PAD) -> int:
    return count * slot + 2 * pad


def strip_scroll(row: int, count: int, width: int, slot: int = STRIP_SLOT, pad: int = STRIP_PAD) -> float:
    """Scroll offset (px) that centres `row`; clamped so the strip never shows empty space at the
    ends, and centred as a whole when every thumbnail fits."""
    total = strip_total(count, slot, pad)
    if total <= width:
        return -(width - total) / 2
    target = pad + row * slot + slot / 2 - width / 2
    return max(0.0, min(target, float(total - width)))


def strip_visible(scroll: float, width: int, count: int, slot: int = STRIP_SLOT, pad: int = STRIP_PAD) -> range:
    if count <= 0:
        return range(0)
    first = max(0, int((scroll - pad) // slot))
    last = min(count - 1, int((scroll + width - pad) // slot))
    return range(first, last + 1)


def strip_slot_at(x: float, scroll: float, count: int, slot: int = STRIP_SLOT, pad: int = STRIP_PAD) -> int | None:
    index = int((x + scroll - pad) // slot)
    return index if 0 <= index < count else None


# // top row: tools (left) | title (centre) | actions + window buttons (right)
TOOLBAR_PAD = 5
TOOLBAR_BUTTON = 36
TOOLBAR_SPACING = 2
TOOLBAR_SEPARATOR = 9
TOOLBAR_EDIT_WIDTH = 82
TOOLBAR_EDIT_GAP = 4
CONTROLS_WIDTH = TOOLBAR_PAD * 2 + 3 * TOOLBAR_BUTTON + 2 * TOOLBAR_SPACING
TITLE_MIN = 180  # the centred title is kept at least this wide before buttons start to collapse
TITLE_FLOOR = 48  # below this the title is dropped altogether
# Optional buttons fold into the "..." menu in this order when the window is narrow.
COLLAPSE_ORDER = ["wallpaper", "print", "slideshow", "copy", "rotate", "favorite"]


def left_width(buttons: int) -> int:
    """Width of the left pill: `buttons` icon buttons, the first one (Info) set off by a separator."""
    return (
        TOOLBAR_PAD * 2
        + buttons * TOOLBAR_BUTTON
        + max(0, buttons - 1) * TOOLBAR_SPACING
        + (TOOLBAR_SEPARATOR if buttons > 1 else 0)
    )


def right_width(icons: int, has_edit: bool) -> int:
    """Width of the right pill: `icons` icon buttons followed by Edit. 0 when it would be empty."""
    if icons <= 0 and not has_edit:
        return 0
    width = TOOLBAR_PAD * 2
    if icons > 0:
        width += icons * TOOLBAR_BUTTON + (icons - 1) * TOOLBAR_SPACING
    if has_edit:
        width += TOOLBAR_EDIT_WIDTH + (TOOLBAR_EDIT_GAP if icons > 0 else 0)
    return width


def _right_start(width: int, right_buttons: int, has_edit: bool, margin: int, gap: int) -> int:
    start = width - margin - CONTROLS_WIDTH
    actions = right_width(right_buttons, has_edit)
    return start - (actions + gap) if actions else start


def title_room(width: int, left_buttons: int, right_buttons: int, has_edit: bool, margin: int, gap: int) -> int:
    """Width the window-centred title could have between the two groups."""
    left_end = margin + left_width(left_buttons)
    right_start = _right_start(width, right_buttons, has_edit, margin, gap)
    centre = width / 2
    return int(2 * (min(centre - left_end, right_start - centre) - gap))


def plan_top(
    width: int,
    left_keys: list[str],
    right_keys: list[str],
    collapse_order: list[str],
    has_edit: bool,
    margin: int,
    gap: int,
    title_min: int = TITLE_MIN,
) -> set[str]:
    """Which optional buttons move into the "..." menu so the centred title keeps `title_min` px.
    `left_keys` / `right_keys` are the visible icon buttons of each pill; Edit never collapses."""
    left, right = list(left_keys), list(right_keys)
    hidden: set[str] = set()
    for key in collapse_order:
        if title_room(width, len(left), len(right), has_edit, margin, gap) >= title_min:
            break
        if key in left:
            left.remove(key)
        elif key in right:
            right.remove(key)
        else:
            continue
        hidden.add(key)
    return hidden


def title_span(width: int, left_end: int, right_start: int, natural: int, gap: int) -> tuple[int, int]:
    """(x, width) of the title: centred on the window when it fits there, otherwise squeezed
    into whatever is free between the two groups."""
    centre = width / 2
    room = int(2 * (min(centre - left_end, right_start - centre) - gap))
    if room >= min(natural, TITLE_MIN):
        span = min(natural, room)
        return round(centre - span / 2), span
    free = max(0, right_start - left_end - 2 * gap)
    span = min(natural, free)
    return left_end + gap + (free - span) // 2, span


# // room the photo keeps clear of the floating controls
NAV_WIDTH = 44  # the round previous / next buttons


def safe_insets(
    margin: int, gap: int, top_height: int, bottom_height: int, info_progress: float, info_width: int
) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) px the "fit" view keeps free: the top row, the bottom row, the
    previous / next buttons at the sides and, while the Info card slides in (`info_progress` 0..1),
    the card, which pushes the next button along with it."""
    top = margin + top_height + gap
    bottom = margin + bottom_height + gap
    side = margin + NAV_WIDTH + gap
    left = side + round(max(0.0, min(1.0, info_progress)) * (info_width + gap))
    return left, top, side, bottom


# // text
def time_parts(timestamp: float) -> tuple[str, str, str, str, str, str] | None:
    """(month name, day, year, hour 1-12, minute 00-59, AM/PM) for the Info card."""
    try:
        moment = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return None
    hour = moment.hour % 12 or 12
    return (
        moment.strftime("%B"),
        str(moment.day),
        str(moment.year),
        str(hour),
        f"{moment.minute:02d}",
        "AM" if moment.hour < 12 else "PM",
    )


def long_date(timestamp: float) -> str:
    """'November 12, 2026 · 10:03 AM'"""
    parts = time_parts(timestamp)
    if parts is None:
        return ""
    month, day, year, hour, minute, meridiem = parts
    return f"{month} {day}, {year} · {hour}:{minute} {meridiem}"


def subtitle(timestamp: float, width: int, height: int) -> str:
    """'November 12, 2026 · 10:03 AM · 4000x3000' (the size is left out while it is unknown)."""
    parts = [long_date(timestamp)] if long_date(timestamp) else []
    if width > 0 and height > 0:
        parts.append(f"{width}x{height}")
    return " · ".join(parts)


def breakable(path: str) -> str:
    """Let a long path wrap at separators by adding zero-width spaces after them."""
    out = []
    for char in path:
        out.append(char)
        if char in "\\/_-.":
            out.append("\u200b")
    return "".join(out)


def bit_depth(mode: str) -> str:
    depth = {"1": 1, "L": 8, "P": 8, "LA": 16, "RGB": 24, "RGBA": 32, "RGBX": 32, "CMYK": 32, "I;16": 16, "I": 32, "F": 32}
    value = depth.get(mode)
    return f"{value} bit" if value else "-"



# // video
SEEK_ZONE = 0.20  # a click in the left / right 20 % of the picture skips back / forward
SEEK_STEP_MS = 10_000


def seek_zone(x: float, width: float) -> int:
    """-1 / +1 when x is inside the left / right SEEK_ZONE of `width`, 0 in the middle (play / pause)."""
    if width <= 0:
        return 0
    if x <= width * SEEK_ZONE:
        return -1
    if x >= width * (1.0 - SEEK_ZONE):
        return 1
    return 0


def clamp_position(position_ms: int, duration_ms: int) -> int:
    return max(0, min(position_ms, duration_ms)) if duration_ms > 0 else max(0, position_ms)


def clock(ms: int) -> str:
    """'3:07' or '1:02:09'."""
    seconds = max(0, int(ms // 1000))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def position_at(x: float, left: float, width: float, duration_ms: int) -> int:
    """Playback position (ms) for a click at x on a seek bar that spans [left, left + width]."""
    if width <= 0 or duration_ms <= 0:
        return 0
    return round(max(0.0, min(1.0, (x - left) / width)) * duration_ms)
