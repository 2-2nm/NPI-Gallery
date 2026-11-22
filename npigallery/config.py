import os
from pathlib import Path

APP_NAME = "NpiGallery"
APP_TITLE = "NPI Gallery"

THUMBNAIL_SIZE = 320
THUMBNAIL_QUALITY = 82

MIN_ROW_HEIGHT = 80
MAX_ROW_HEIGHT = 320
DEFAULT_ROW_HEIGHT = 160
ROW_PRESETS = (("Small", 110), ("Medium", 160), ("Large", 240))
TILE_GAP = 6


def cache_directory() -> Path:
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_CACHE_HOME")
        or str(Path.home() / ".cache")
    )
    directory = Path(base) / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def thumbnail_database_path() -> Path:
    return cache_directory() / "thumbnails.db"


def media_index_path() -> Path:
    return cache_directory() / "media-index.db"
