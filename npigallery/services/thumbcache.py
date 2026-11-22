import sqlite3
import threading
from pathlib import Path

from ..filesystem import is_under, root_keys

COMMIT_INTERVAL = 32


class ThumbnailCache:
    def __init__(self, database: Path):
        self._lock = threading.Lock()
        self._pending = 0
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS thumbnails ("
            "path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
            "size INTEGER NOT NULL, data BLOB NOT NULL)"
        )
        self._connection.commit()

    def get(self, path: str, mtime_ns: int, size: int) -> bytes | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM thumbnails WHERE path = ? AND mtime_ns = ? AND size = ?",
                (path, mtime_ns, size),
            ).fetchone()
        return bytes(row[0]) if row else None

    def put(self, path: str, mtime_ns: int, size: int, data: bytes) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO thumbnails VALUES (?, ?, ?, ?)",
                (path, mtime_ns, size, data),
            )
            self._pending += 1
            if self._pending >= COMMIT_INTERVAL:
                self._connection.commit()
                self._pending = 0

    def prune(self, roots: list[Path], present: set[str]) -> int:
        """Forget thumbnails of photos under `roots` that are gone from disk; returns how many.

        `present` is every path the latest scan found. Roots that are not reachable right now
        (an unplugged drive) are left alone, and a path is only dropped if the file is really gone.
        """
        usable = [root for root in roots if root.is_dir()]
        if not usable:
            return 0
        keys = root_keys(usable)
        with self._lock:
            known = [row[0] for row in self._connection.execute("SELECT path FROM thumbnails")]
        stale = []
        for key in known:
            if key in present:
                continue
            if is_under(key, keys) and not Path(key).exists():
                stale.append((key,))
        if stale:
            with self._lock:
                self._connection.executemany("DELETE FROM thumbnails WHERE path = ?", stale)
                self._connection.commit()
                self._pending = 0
        return len(stale)

    def usage(self) -> tuple[int, int]:
        """(number of saved thumbnails, bytes of thumbnail data). LENGTH() of a BLOB comes from the
        record header, so this does not read the images themselves."""
        with self._lock:
            count, size = self._connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(data)), 0) FROM thumbnails"
            ).fetchone()
        return int(count), int(size)

    def flush(self) -> None:
        with self._lock:
            self._connection.commit()
            self._pending = 0

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM thumbnails")
            self._connection.commit()
            self._pending = 0
            self._connection.execute("VACUUM")

    def close(self) -> None:
        with self._lock:
            self._connection.commit()
            self._connection.close()