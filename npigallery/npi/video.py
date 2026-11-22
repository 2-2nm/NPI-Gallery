"""Video support: metadata for the scanner and a poster frame for the thumbnail.

PyAV (`pip install av`, it ships its own ffmpeg) is imported lazily, so the gallery still starts
without it; videos are then simply not listed instead of the app failing to launch.
"""

import io
import math
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageStat

from ..config import THUMBNAIL_QUALITY, THUMBNAIL_SIZE
from .errors import UnsupportedImageError

MIN_PLAUSIBLE_YEAR = 1990  # containers without a real date often say 1904 or 1970
DARK_FRAME = 10.0  # mean brightness (0-255) below which a poster frame counts as "black"
MAX_POSTER_ATTEMPTS = 3

_av = None
_av_missing = False


def _load_av():
    global _av, _av_missing
    if _av is not None:
        return _av
    if _av_missing:
        raise UnsupportedImageError("PyAV is not installed (pip install av)")
    try:
        import av
    except ImportError as error:
        _av_missing = True
        raise UnsupportedImageError("PyAV is not installed (pip install av)") from error
    _av = av
    return av


def video_support_available() -> bool:
    try:
        _load_av()
    except UnsupportedImageError:
        return False
    return True


def _video_stream(container):
    for stream in container.streams:
        if stream.type == "video":
            # Attached pictures (cover art in an audio file) are "video" streams too, but not videos.
            disposition = getattr(stream, "disposition", None)
            if disposition is not None and "attached_pic" in str(disposition).lower():
                continue
            return stream
    return None


def _clockwise_rotation(container, stream) -> int:
    """How far the picture must be turned clockwise to be shown upright: 0, 90, 180 or 270.

    Phones store portrait video sideways plus a rotation hint; decoders do not apply it by themselves.
    """
    degrees: float | None = None
    try:
        tag = stream.metadata.get("rotate")  # older ffmpeg: clockwise degrees as text
        if tag is not None:
            degrees = float(tag)
    except Exception:
        degrees = None
    if degrees is None:
        degrees = _display_matrix_rotation(stream)
    if degrees is None:
        return 0
    return int(round(degrees / 90.0)) % 4 * 90


def _display_matrix_rotation(stream) -> float | None:
    """Clockwise degrees from the stream's display matrix side data, when PyAV exposes it."""
    try:
        side_data = stream.side_data
        raw = None
        for key in ("DISPLAYMATRIX", "Display Matrix", "displaymatrix"):
            try:
                raw = side_data[key]
                break
            except Exception:
                continue
        if raw is None:
            for entry in side_data:
                if "display" in str(getattr(entry, "type", entry)).lower():
                    raw = entry
                    break
        if raw is None:
            return None
        matrix = struct.unpack("<9i", bytes(raw)[:36])
        scale_x = math.hypot(matrix[0], matrix[3]) / 65536.0
        scale_y = math.hypot(matrix[1], matrix[4]) / 65536.0
        if not scale_x or not scale_y:
            return None
        counter_clockwise = -math.degrees(math.atan2(matrix[1] / 65536.0 / scale_y, matrix[0] / 65536.0 / scale_x))
        return -counter_clockwise
    except Exception:
        return None


def _creation_time(container) -> float | None:
    try:
        value = container.metadata.get("creation_time")
    except Exception:
        return None
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        stamp = moment.timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    if moment.year < MIN_PLAUSIBLE_YEAR or stamp > time.time() + 86400:
        return None
    return stamp


def _duration_seconds(container, stream) -> float:
    try:
        if container.duration:
            return max(0.0, container.duration / 1_000_000.0)  # av.time_base
    except Exception:
        pass
    try:
        if stream.duration and stream.time_base:
            return max(0.0, float(stream.duration * stream.time_base))
    except Exception:
        pass
    return 0.0


def read_video_facts(path) -> tuple[int, int, float | None, float, str]:
    """Return (width, height, taken_at, duration seconds, format label) for a video on disk.

    Same shape of answer as `read_image_facts`: zero width and height mean "unreadable".
    Width and height are the ones the video is shown with (a sideways-stored portrait clip is swapped).
    """
    try:
        av = _load_av()
        with av.open(str(path)) as container:
            stream = _video_stream(container)
            if stream is None:
                return 0, 0, None, 0.0, ""
            codec = stream.codec_context
            width, height = int(codec.width or 0), int(codec.height or 0)
            if _clockwise_rotation(container, stream) in (90, 270):
                width, height = height, width
            label = Path(path).suffix.lstrip(".").upper()
            return width, height, _creation_time(container), _duration_seconds(container, stream), label
    except Exception:
        return 0, 0, None, 0.0, ""


def _poster_times(duration: float) -> list[float]:
    """Where to look for a good poster frame: ~10 % in (the very first frame is often black)."""
    if duration <= 0:
        return [0.0]
    first = min(max(duration * 0.1, 1.0), duration * 0.5)
    times = [first, duration * 0.5, 0.0]
    unique: list[float] = []
    for moment in times:
        if all(abs(moment - other) > 0.01 for other in unique):
            unique.append(moment)
    return unique[:MAX_POSTER_ATTEMPTS]


def _grab_frame(container, stream, seconds: float) -> Image.Image | None:
    """The first frame at or after the keyframe preceding `seconds`."""
    try:
        if seconds > 0 and stream.time_base:
            container.seek(int(seconds / stream.time_base), stream=stream, backward=True, any_frame=False)
        else:
            container.seek(0, stream=stream)
    except Exception:
        pass  # not seekable: decoding from wherever the container is still gives a frame
    for frame in container.decode(stream):
        return frame.to_image()
    return None


def _brightness(image: Image.Image) -> float:
    small = image.convert("L")
    small.thumbnail((32, 32))
    return float(ImageStat.Stat(small).mean[0])


def build_video_thumbnail(source, size: int = THUMBNAIL_SIZE) -> bytes:
    """A WEBP poster frame no larger than `size`, upright, as a gallery thumbnail."""
    av = _load_av()
    try:
        best: Image.Image | None = None
        best_brightness = -1.0
        with av.open(str(source)) as container:
            stream = _video_stream(container)
            if stream is None:
                raise UnsupportedImageError("no video stream")
            stream.thread_type = "AUTO"
            rotation = _clockwise_rotation(container, stream)
            duration = _duration_seconds(container, stream)
            for moment in _poster_times(duration):
                frame = _grab_frame(container, stream, moment)
                if frame is None:
                    continue
                brightness = _brightness(frame)
                if brightness > best_brightness:
                    best, best_brightness = frame, brightness
                if brightness >= DARK_FRAME:
                    break
        if best is None:
            raise UnsupportedImageError("no decodable frame")
        if rotation:
            best = best.rotate(-rotation, expand=True)  # Pillow turns counter-clockwise for positive angles
        best.thumbnail((size, size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        best.convert("RGB").save(buffer, "WEBP", quality=THUMBNAIL_QUALITY, method=4)
        return buffer.getvalue()
    except UnsupportedImageError:
        raise
    except Exception as error:
        raise UnsupportedImageError(str(error)) from error