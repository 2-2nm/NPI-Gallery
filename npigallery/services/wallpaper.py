"""Set a photo as the Windows desktop wallpaper.

Windows only understands plain .jpg / .png / .bmp files here, so anything else (a .npi container,
webp, heic, tiff, a JPEG that relies on its EXIF orientation, ...) is first written out as a PNG in
the cache folder. That file is kept (Windows can read the path again later) and the previous one is
removed once the new wallpaper is in place. Other platforms are not supported.
"""

import ctypes
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageOps

from ..config import cache_directory
from ..filesystem import write_atomic
from ..npi import is_npi_path, read_image_bytes, read_meta

SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

NATIVE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp"})
TAG_ORIENTATION = 0x0112


class WallpaperError(Exception):
    """The wallpaper could not be set; the message is meant to be shown to the user."""


def is_supported() -> bool:
    return sys.platform == "win32"


def _folder() -> Path:
    folder = cache_directory() / "wallpaper"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _needs_conversion(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in NATIVE_EXTENSIONS:
        return True
    if suffix in {".jpg", ".jpeg"}:
        try:
            with Image.open(path) as image:
                return image.getexif().get(TAG_ORIENTATION, 1) not in (0, 1)
        except Exception:
            return True
    return False


def _png_target(key: str) -> Path:
    return _folder() / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}.png"


def prepare_image(path: Path) -> Path:
    """The file to hand to Windows for `path`: the file itself when Windows can use it, otherwise
    a PNG copy in the cache folder."""
    path = Path(path)
    try:
        if is_npi_path(path):
            meta = read_meta(path)
            target = _png_target(f"npi|{meta.sha256}")
            if not target.is_file():
                # Pillow can't read the container itself, so the payload goes to a scratch file first.
                scratch = target.with_suffix(".src")
                write_atomic(scratch, read_image_bytes(path))
                try:
                    _convert(scratch, target)
                finally:
                    scratch.unlink(missing_ok=True)
            return target
        if not _needs_conversion(path):
            return path
        stat = path.stat()
        target = _png_target(f"file|{path}|{stat.st_mtime_ns}|{stat.st_size}")
        if not target.is_file():
            _convert(path, target)
        return target
    except WallpaperError:
        raise
    except Exception as error:
        raise WallpaperError(f"Could not prepare the picture: {error}") from error


def _convert(source: Path, target: Path) -> None:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        partial = target.with_suffix(".tmp")
        image.save(partial, "PNG")
    partial.replace(target)


def _prune(keep: Path) -> None:
    for file in _folder().glob("*"):
        if file != keep:
            file.unlink(missing_ok=True)


def set_desktop_wallpaper(path: Path) -> None:
    if not is_supported():
        raise WallpaperError("Setting the wallpaper is only supported on Windows.")
    ready = prepare_image(Path(path)).resolve()
    user32 = ctypes.windll.user32
    user32.SystemParametersInfoW.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    user32.SystemParametersInfoW.restype = ctypes.c_int
    buffer = ctypes.create_unicode_buffer(str(ready))
    ok = user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, ctypes.cast(buffer, ctypes.c_void_p), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    )
    if not ok:
        raise WallpaperError(f"Windows refused to change the wallpaper ({ctypes.WinError().strerror}).")
    _prune(ready)
