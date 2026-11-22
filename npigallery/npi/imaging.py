import io
import math
from dataclasses import dataclass
from datetime import datetime

from PIL import ExifTags, Image, ImageOps

from ..config import THUMBNAIL_QUALITY, THUMBNAIL_SIZE
from .errors import UnsupportedImageError
from .formats import format_label

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

Image.MAX_IMAGE_PIXELS = 1_000_000_000

TAG_ORIENTATION = 0x0112
TAG_DATETIME = 0x0132
TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_MAKER_NOTE = 0x927C
TAG_INTEROP_IFD = 0xA005

HIDDEN_TAGS = frozenset({TAG_EXIF_IFD, TAG_GPS_IFD, TAG_MAKER_NOTE, TAG_INTEROP_IFD})
TRANSPOSED_ORIENTATIONS = frozenset({5, 6, 7, 8})
MAX_VALUE_LENGTH = 200


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    exif: bytes
    icc: bytes
    taken_at: float | None


def _extract_exif_bytes(image: Image.Image) -> bytes:
    raw = image.info.get("exif")
    if isinstance(raw, bytes) and raw:
        return raw
    exif = image.getexif()
    if len(exif):
        return exif.tobytes()
    return b""


def _parse_exif_date(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _exif_ifd(exif: Image.Exif) -> dict:
    try:
        return dict(exif.get_ifd(TAG_EXIF_IFD))
    except Exception:
        return {}


def _taken_at(exif: Image.Exif) -> float | None:
    exif_ifd = _exif_ifd(exif)
    candidates = (
        exif_ifd.get(TAG_DATETIME_ORIGINAL),
        exif_ifd.get(TAG_DATETIME_DIGITIZED),
        exif.get(TAG_DATETIME),
    )
    for value in candidates:
        parsed = _parse_exif_date(value)
        if parsed is not None:
            return parsed
    return None


def inspect_image(data: bytes) -> ImageInfo:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            exif = image.getexif()
            if exif.get(TAG_ORIENTATION) in TRANSPOSED_ORIENTATIONS:
                width, height = height, width
            return ImageInfo(
                width,
                height,
                _extract_exif_bytes(image),
                image.info.get("icc_profile") or b"",
                _taken_at(exif),
            )
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise UnsupportedImageError(str(error)) from error


def read_dimensions(path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            if image.getexif().get(TAG_ORIENTATION) in TRANSPOSED_ORIENTATIONS:
                width, height = height, width
            return width, height
    except Exception:
        return 0, 0


def _png_exif(handle) -> Image.Exif:
    """EXIF of a PNG found by walking its chunks, never decoding the pixels.

    Pillow's PngImageFile.getexif() calls load() (a full decode) whenever the eXIf chunk was not
    seen before IDAT, which is the case for every PNG without EXIF: ~75 ms each, 60% of a cold scan.
    Here the pixel data is skipped with seek(), so a PNG costs a few small reads.
    """
    exif = Image.Exif()
    try:
        handle.seek(8)  # past the PNG signature
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            length, kind = int.from_bytes(header[:4], "big"), header[4:]
            if kind == b"eXIf":
                exif.load(handle.read(length))
                break
            if kind == b"IEND":
                break
            handle.seek(length + 4, 1)  # chunk data + CRC
    except Exception:
        return Image.Exif()
    return exif


def read_image_facts(path) -> tuple[int, int, float | None, tuple[float, float] | None, str]:
    """Return (width, height, taken_at, coordinates, format label) for an image on disk, opening it only once."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(64)
            handle.seek(0)
            with Image.open(handle) as image:
                width, height = image.size
                if image.format == "PNG" and "exif" not in image.info and "Raw profile type exif" not in image.info:
                    exif = _png_exif(handle)
                else:
                    exif = image.getexif()
                if exif.get(TAG_ORIENTATION) in TRANSPOSED_ORIENTATIONS:
                    width, height = height, width
                label = format_label(head, image.format or "")
                return width, height, _taken_at(exif), _exif_coordinates(exif), label
    except Exception:
        return 0, 0, None, None, ""


def _normalize_mode(image: Image.Image) -> Image.Image:
    if "A" in image.getbands() or "transparency" in image.info:
        return image.convert("RGBA")
    return image.convert("RGB")


def build_thumbnail(source, size: int = THUMBNAIL_SIZE) -> bytes:
    try:
        with Image.open(source) as image:
            if image.format == "JPEG":
                image.draft("RGB", (size * 2, size * 2))
            oriented = ImageOps.exif_transpose(image)
            oriented.thumbnail((size, size), Image.Resampling.LANCZOS)
            oriented = _normalize_mode(oriented)
            buffer = io.BytesIO()
            oriented.save(buffer, "WEBP", quality=THUMBNAIL_QUALITY, method=4)
            return buffer.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise UnsupportedImageError(str(error)) from error


def load_oriented_rgba(source) -> Image.Image:
    with Image.open(source) as image:
        oriented = ImageOps.exif_transpose(image)
        return oriented.convert("RGBA")


def load_oriented_display(source) -> Image.Image:
    """Decode a photo for on-screen use: EXIF-oriented, RGB (or RGBA only when it has transparency).

    Same pixels as `load_oriented_rgba`, but it makes no copy that is not needed: no transpose
    copy when there is nothing to rotate, no RGBA conversion for an opaque photo (3 bytes per
    pixel instead of 4), and the pre-rotation pixels are freed as soon as the rotated ones exist.
    """
    with Image.open(source) as image:
        image.load()
        if image.getexif().get(TAG_ORIENTATION) in _ORIENTATIONS:
            oriented = ImageOps.exif_transpose(image)
            image.close()  # free the unrotated pixels now instead of at the end of the decode
        else:
            oriented = image
        target = _display_mode(oriented)
        if oriented.mode == target:
            return oriented
        try:
            converted = oriented.convert(target)
        except ValueError:
            converted = oriented.convert("RGBA")  # a mode with no direct route to RGB
        if converted is not oriented:
            oriented.close()
        return converted


_ORIENTATIONS = frozenset(range(2, 9))
_BAND_BYTES = 4 * 1024 * 1024


def pixel_buffer(image: Image.Image) -> bytearray:
    """The raw pixels of `image` (same bytes as `image.tobytes()`) in one buffer.

    `tobytes()` gathers the encoder output in chunks and then joins them, so for a moment it holds
    two full copies. Filling a preallocated buffer a few rows at a time holds just one.
    """
    width, height = image.size
    row = width * len(image.getbands())
    buffer = bytearray(row * height)
    step = max(1, _BAND_BYTES // max(1, row))
    for top in range(0, height, step):
        bottom = min(height, top + step)
        buffer[top * row : bottom * row] = image.crop((0, top, width, bottom)).tobytes()
    return buffer


def _display_mode(image: Image.Image) -> str:
    if image.mode in ("RGB", "RGBA"):
        return image.mode
    if "A" in image.getbands() or "transparency" in image.info:
        return "RGBA"
    return "RGB"


def _format_exif_value(value: object) -> str:
    if isinstance(value, bytes):
        return ""
    text = str(value).strip()
    if len(text) > MAX_VALUE_LENGTH:
        text = text[:MAX_VALUE_LENGTH] + "..."
    return text


def read_exif_tags(raw: bytes) -> list[tuple[str, str]]:
    if not raw:
        return []
    exif = Image.Exif()
    try:
        exif.load(raw)
    except Exception:
        return []
    entries = dict(exif.items())
    entries.update(_exif_ifd(exif))
    rows = []
    for tag, value in entries.items():
        if tag in HIDDEN_TAGS:
            continue
        text = _format_exif_value(value)
        if text:
            rows.append((ExifTags.TAGS.get(tag, f"0x{tag:04X}"), text))
    return rows


def _gps_degrees(value, ref) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    result = degrees + minutes / 60.0 + seconds / 3600.0
    if str(ref).strip().upper() in ("S", "W"):
        result = -result
    return result


def read_gps(raw: bytes) -> list[tuple[str, str]]:
    """GPS rows (latitude, longitude, altitude) from raw EXIF bytes; empty when the photo has none."""
    if not raw:
        return []
    exif = Image.Exif()
    try:
        exif.load(raw)
        gps = dict(exif.get_ifd(TAG_GPS_IFD))
    except Exception:
        return []
    rows: list[tuple[str, str]] = []
    latitude = _gps_degrees(gps.get(2), gps.get(1))
    longitude = _gps_degrees(gps.get(4), gps.get(3))
    if latitude is not None and longitude is not None:
        rows.append(("Latitude", f"{latitude:.6f}"))
        rows.append(("Longitude", f"{longitude:.6f}"))
    altitude = gps.get(6)
    if altitude is not None:
        try:
            rows.append(("Altitude", f"{float(altitude):.1f} m"))
        except (TypeError, ValueError):
            pass
    return rows


def _coordinates_from_ifd(gps) -> tuple[float, float] | None:
    latitude = _gps_degrees(gps.get(2), gps.get(1))
    longitude = _gps_degrees(gps.get(4), gps.get(3))
    if latitude is None or longitude is None:
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if abs(latitude) > 90 or abs(longitude) > 180:
        return None
    if latitude == 0 and longitude == 0:
        return None
    return latitude, longitude


def _exif_coordinates(exif) -> tuple[float, float] | None:
    try:
        return _coordinates_from_ifd(dict(exif.get_ifd(TAG_GPS_IFD)))
    except Exception:
        return None


def read_coordinates(raw: bytes) -> tuple[float, float] | None:
    if not raw:
        return None
    exif = Image.Exif()
    try:
        exif.load(raw)
    except Exception:
        return None
    return _exif_coordinates(exif)