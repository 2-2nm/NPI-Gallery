from enum import IntEnum
from pathlib import Path

NPI_EXTENSION = ".npi"

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif", ".bmp",
        ".tif", ".tiff", ".heic", ".heif", ".avif",
    }
)

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv",
        ".mpg", ".mpeg", ".3gp", ".mts", ".m2ts",
    }
)

HEIC_BRANDS = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
)
AVIF_BRANDS = frozenset({b"avif", b"avis"})


class ImageFormat(IntEnum):
    UNKNOWN = 0
    JPEG = 1
    PNG = 2
    WEBP = 3
    GIF = 4
    BMP = 5
    TIFF = 6
    HEIC = 7
    AVIF = 8

    @classmethod
    def from_code(cls, code: int) -> "ImageFormat":
        try:
            return cls(code)
        except ValueError:
            return cls.UNKNOWN


def detect_format(data: bytes) -> ImageFormat:
    head = data[:64]
    if head.startswith(b"\xff\xd8\xff"):
        return ImageFormat.JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageFormat.PNG
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ImageFormat.GIF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ImageFormat.WEBP
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return ImageFormat.TIFF
    if head[:2] == b"BM":
        return ImageFormat.BMP
    if head[4:8] == b"ftyp":
        size = int.from_bytes(head[:4], "big")
        end = min(size, len(head)) if size >= 16 else len(head)
        brands = {head[8:12]}
        brands.update(head[offset:offset + 4] for offset in range(16, end - 3, 4))
        if brands & AVIF_BRANDS:
            return ImageFormat.AVIF
        if brands & HEIC_BRANDS:
            return ImageFormat.HEIC
    return ImageFormat.UNKNOWN


def format_label(head: bytes, fallback: str = "") -> str:
    detected = detect_format(head)
    if detected is not ImageFormat.UNKNOWN:
        return detected.name
    return fallback.upper()


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video_path(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_npi_path(path: Path) -> bool:
    return path.suffix.lower() == NPI_EXTENSION