from datetime import datetime

SIZE_UNITS = ("B", "KB", "MB", "GB")


def human_size(size: int) -> str:
    value = float(size)
    index = 0
    while value >= 1024 and index < len(SIZE_UNITS) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {SIZE_UNITS[index]}"


def format_duration(seconds: float) -> str:
    """Clip length as shown on a video tile: 0:07, 12:34 or 1:02:03."""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_timestamp(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "-"