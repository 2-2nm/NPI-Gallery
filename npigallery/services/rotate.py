"""Permanently rotate a photo on disk: the pixels are rotated and the file is rewritten, so every
other program sees the same orientation (no reliance on the EXIF Orientation tag).

Plain image files are rewritten in place; for a .npi container the payload is rotated and the
container (meta, thumbnail, checksum) is rebuilt. The file keeps its modification time so a
rotated photo does not jump around in a date-sorted gallery, except that the time is nudged when
the size did not change, because the gallery's caches are keyed by (mtime, size).
"""

import hashlib
import io
import os
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin

from ..filesystem import write_atomic
from ..npi import (
    build_thumbnail,
    detect_format,
    inspect_image,
    is_npi_path,
    read_record,
    write_npi,
)
from ..npi.container import read_payload
from ..npi.formats import ImageFormat

TAG_ORIENTATION = 0x0112
TAG_EXIF_IFD = 0x8769
TAG_PIXEL_X, TAG_PIXEL_Y = 0xA002, 0xA003
# TIFF keeps its image structure in the same tag table EXIF is read from; copying those back
# would describe the old size and layout, so they never travel with the metadata.
TIFF_STRUCTURE = frozenset(
    {256, 257, 258, 259, 262, 266, 273, 274, 277, 278, 279, 284, 317, 320, 322, 323, 324, 325, 338, 339}
)
JPEG_QUALITY = 95
CACHE_NUDGE_NS = 1000

SUPPORTED = frozenset(
    {ImageFormat.JPEG, ImageFormat.PNG, ImageFormat.WEBP, ImageFormat.GIF, ImageFormat.BMP, ImageFormat.TIFF}
)


class RotateError(Exception):
    """The photo cannot be rotated; the message is meant for the person."""


def _transpose_for(degrees_cw: int) -> Image.Transpose:
    # PIL's ROTATE_90 turns counter-clockwise.
    return {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }[degrees_cw]


def _fix_exif(exif: Image.Exif, width: int, height: int, is_tiff: bool = False) -> bytes:
    """EXIF for the rotated pixels: orientation baked in (tag removed), pixel size updated."""
    try:
        if TAG_ORIENTATION in exif:
            del exif[TAG_ORIENTATION]
        if is_tiff:
            for tag in TIFF_STRUCTURE.intersection(list(exif.keys())):
                del exif[tag]
        ifd = exif.get_ifd(TAG_EXIF_IFD)
        if TAG_PIXEL_X in ifd:
            ifd[TAG_PIXEL_X] = width
        if TAG_PIXEL_Y in ifd:
            ifd[TAG_PIXEL_Y] = height
        return exif.tobytes() if len(exif) else b""
    except Exception:
        return b""


def rotate_image_bytes(data: bytes, degrees_cw: int) -> bytes:
    """`data` (a whole image file) rotated clockwise by a multiple of 90 degrees, same format."""
    if degrees_cw % 90:
        raise RotateError("Photos can only be rotated in steps of 90 degrees.")
    degrees = degrees_cw % 360
    if degrees == 0:
        return data
    image_format = detect_format(data)
    if image_format not in SUPPORTED:
        name = "This kind of" if image_format is ImageFormat.UNKNOWN else image_format.name
        raise RotateError(f"{name} photos can't be rotated permanently.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if getattr(source, "n_frames", 1) > 1:
                raise RotateError("Animated or multi-page images can't be rotated permanently.")
            source.load()
            exif = source.getexif()
            icc = source.info.get("icc_profile")
            dpi = source.info.get("dpi")
            xmp = source.info.get("xmp")
            transparency = source.info.get("transparency")
            compression = source.info.get("compression")
            subsampling = JpegImagePlugin.get_sampling(source) if image_format is ImageFormat.JPEG else -1
            # What the viewer shows is what gets saved: bake in any EXIF orientation first.
            rotated = ImageOps.exif_transpose(source).transpose(_transpose_for(degrees))
    except RotateError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise RotateError(f"The photo can't be read: {error}") from error

    exif_bytes = _fix_exif(exif, *rotated.size, is_tiff=image_format is ImageFormat.TIFF)
    extras: dict = {}
    if exif_bytes:
        extras["exif"] = exif_bytes
    if icc:
        extras["icc_profile"] = icc
    if dpi:
        extras["dpi"] = dpi
    options: dict = {}
    if image_format is ImageFormat.JPEG:
        options = {"quality": JPEG_QUALITY, "optimize": True}
        if subsampling in (0, 1, 2):
            options["subsampling"] = subsampling
        if isinstance(xmp, bytes) and xmp:
            extras["xmp"] = xmp
    elif image_format is ImageFormat.PNG:
        options = {"compress_level": 6}
    elif image_format is ImageFormat.WEBP:
        lossless = data[12:16] == b"VP8L"
        options = {"lossless": True, "quality": 100} if lossless else {"quality": JPEG_QUALITY}
    elif image_format is ImageFormat.GIF:
        if transparency is not None:
            options["transparency"] = transparency
        extras.pop("exif", None)
        extras.pop("dpi", None)
    elif image_format is ImageFormat.TIFF and isinstance(compression, str):
        options["compression"] = compression
    elif image_format is ImageFormat.BMP:
        extras.pop("exif", None)
        extras.pop("icc_profile", None)

    name = image_format.name
    for attempt in (dict(options, **extras), options):  # second try: without the metadata extras
        buffer = io.BytesIO()
        try:
            rotated.save(buffer, name, **attempt)
            return buffer.getvalue()
        except (OSError, ValueError, TypeError, KeyError):
            continue
    raise RotateError(f"The rotated {name} photo could not be saved.")


def _rewrite(path: Path, write) -> None:
    """Run `write()` (which replaces the file) and carry the old timestamps over."""
    before = path.stat()
    write()
    after = path.stat()
    mtime = before.st_mtime_ns
    if after.st_size == before.st_size:
        mtime += CACHE_NUDGE_NS  # same size: make sure (mtime, size) still changes
    os.utime(path, ns=(before.st_atime_ns, mtime))


def rotate_file(path: Path, degrees_cw: int) -> None:
    """Rotate the photo at `path` (an image file or a .npi container) clockwise, in place."""
    if degrees_cw % 360 == 0:
        return
    try:
        if is_npi_path(path):
            record = read_record(path)
            payload = read_payload(path, record)
            rotated = rotate_image_bytes(payload, degrees_cw)
            info = inspect_image(rotated)
            meta = replace(
                record.meta,
                size=len(rotated),
                width=info.width,
                height=info.height,
                sha256=hashlib.sha256(rotated).hexdigest(),
                taken_at=info.taken_at if info.taken_at is not None else record.meta.taken_at,
            )
            thumbnail = build_thumbnail(io.BytesIO(rotated))
            _rewrite(path, lambda: write_npi(path, meta, info.exif, info.icc, thumbnail, rotated))
        else:
            rotated = rotate_image_bytes(path.read_bytes(), degrees_cw)
            _rewrite(path, lambda: write_atomic(path, rotated))
    except RotateError:
        raise
    except OSError as error:
        raise RotateError(f"The file could not be written: {error}") from error
    except Exception as error:  # a broken container, an unreadable payload...
        raise RotateError(str(error)) from error
