"""Persistent scan index: lets the gallery appear instantly on start-up.

The last scan result is stored in SQLite. On launch it is shown immediately while a
background scan only re-opens files whose mtime or size changed.
"""

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from ..filesystem import is_under, root_keys
from .scanner import MediaItem

SCHEMA_VERSION = 5

_COLUMNS = (
    "path, name, is_npi, size, sort_time, format_label, width, height, mtime_ns, file_size, latitude, longitude, "
    "is_video, duration"
)


class MediaIndex:
    def __init__(self, database: Path):
        self._database = database
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS media ("
                "path TEXT PRIMARY KEY, name TEXT NOT NULL, is_npi INTEGER NOT NULL, "
                "size INTEGER NOT NULL, sort_time REAL NOT NULL, format_label TEXT NOT NULL, "
                "width INTEGER NOT NULL, height INTEGER NOT NULL, "
                "mtime_ns INTEGER NOT NULL, file_size INTEGER NOT NULL, "
                "latitude REAL, longitude REAL, is_video INTEGER NOT NULL DEFAULT 0, duration REAL NOT NULL DEFAULT 0)"
            )
            known = {row[1] for row in connection.execute("PRAGMA table_info(media)")}
            if "latitude" not in known:
                connection.execute("DELETE FROM media")
                connection.execute("ALTER TABLE media ADD COLUMN latitude REAL")
                connection.execute("ALTER TABLE media ADD COLUMN longitude REAL")
            if "is_video" not in known:
                # Version 3: videos. Rows that exist are photos, so they stay valid; the next scan adds the videos.
                connection.execute("ALTER TABLE media ADD COLUMN is_video INTEGER NOT NULL DEFAULT 0")
                connection.execute("ALTER TABLE media ADD COLUMN duration REAL NOT NULL DEFAULT 0")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                connection.execute("DELETE FROM media WHERE is_npi = 0")
            if version < 5:
                # Version 5: the date logic went back to EXIF -> file mtime. Rows made by the interim logic
                # (versions 4) carry other sort_time values and are keyed by (mtime, size), so drop and rescan once.
                connection.execute("DELETE FROM media WHERE is_npi = 0")
            if version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        # One short-lived connection per call keeps this safe from any thread.
        connection = sqlite3.connect(self._database, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def load(self, roots: list[Path], excluded: Iterable[Path] = ()) -> list[MediaItem]:
        try:
            connection = self._connect()
            try:
                rows = connection.execute(f"SELECT {_COLUMNS} FROM media").fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            return []
        keys = root_keys(roots)
        excluded_keys = root_keys(list(excluded))
        items = []
        for (
            path, name, is_npi, size, sort_time, label, width, height, mtime_ns, file_size, latitude, longitude,
            is_video, duration,
        ) in rows:
            if is_under(path, keys) and not (excluded_keys and is_under(path, excluded_keys)):
                items.append(
                    MediaItem(
                        Path(path), name, bool(is_npi), size, sort_time, label, width, height, mtime_ns, file_size,
                        latitude, longitude, bool(is_video), duration,
                    )
                )
        items.sort(key=lambda item: item.sort_time, reverse=True)
        return items

    def save(self, items: list[MediaItem], roots: list[Path], excluded: Iterable[Path] = ()) -> None:
        """Make the rows under `roots` match `items`, writing only what changed.

        A photo is rewritten when it is new or its mtime / size differ from the stored row; rows of
        photos that are gone are deleted. An unchanged library therefore writes nothing at all.
        """
        keys = root_keys(roots)
        excluded_keys = root_keys(list(excluded))
        try:
            connection = self._connect()
            try:
                with connection:
                    stored = {
                        path: (mtime_ns, file_size)
                        for path, mtime_ns, file_size in connection.execute(
                            "SELECT path, mtime_ns, file_size FROM media"
                        )
                        if is_under(path, keys) and not (excluded_keys and is_under(path, excluded_keys))
                    }
                    present: set[str] = set()
                    changed = []
                    for item in items:
                        key = str(item.path)
                        present.add(key)
                        if stored.get(key) != (item.mtime_ns, item.file_size):
                            changed.append(
                                (
                                    key, item.name, int(item.is_npi), item.size, item.sort_time,
                                    item.format_label, item.width, item.height, item.mtime_ns, item.file_size,
                                    item.latitude, item.longitude, int(item.is_video), item.duration,
                                )
                            )
                    gone = [(path,) for path in stored if path not in present]
                    if gone:
                        connection.executemany("DELETE FROM media WHERE path = ?", gone)
                    if changed:
                        connection.executemany(
                            f"INSERT OR REPLACE INTO media ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            changed,
                        )
            finally:
                connection.close()
        except sqlite3.Error:
            pass

    def clear(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM media")
        except sqlite3.Error:
            pass