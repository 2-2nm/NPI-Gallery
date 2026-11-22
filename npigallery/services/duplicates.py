"""Find photos that are exact copies of each other (same bytes).

Only files of the same size can be copies, so only those are read and hashed (SHA-256). Hashes are kept
in the same SQLite file as the media index, keyed by path + mtime + size, so a second scan reads nothing
that did not change. Each group of copies has one photo to keep (the biggest picture, then the oldest
file); the others are the "copies" that get the badge on their tile.

Two kinds of "not a duplicate" are remembered: a whole group (by content hash, so it survives moving or
renaming the files) and one single file (by path).
"""

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .scanner import MediaItem

READ_CHUNK = 1 << 20
SAVE_EVERY = 64


@dataclass(frozen=True)
class DuplicateGroup:
    sha256: str
    items: tuple[MediaItem, ...]  # items[0] is the one to keep

    @property
    def keep(self) -> MediaItem:
        return self.items[0]

    @property
    def copies(self) -> tuple[MediaItem, ...]:
        return self.items[1:]

    @property
    def freeable(self) -> int:
        return sum(item.file_size for item in self.copies)


@dataclass(frozen=True)
class DuplicateResult:
    groups: tuple[DuplicateGroup, ...] = ()

    @property
    def freeable(self) -> int:
        return sum(group.freeable for group in self.groups)

    @property
    def copy_count(self) -> int:
        return sum(len(group.copies) for group in self.groups)

    def copy_paths(self) -> set[str]:
        """Paths of every photo that is a copy (not the kept one) of another."""
        return {str(item.path) for group in self.groups for item in group.copies}


def keep_order(item: MediaItem) -> tuple:
    # Biggest picture first, then the oldest file, then the shortest (most original) name.
    return (-(item.width * item.height), item.mtime_ns, len(item.name), str(item.path))


def _candidates(items: Iterable[MediaItem]) -> list[MediaItem]:
    return [item for item in items if not item.is_video and not item.is_npi and item.file_size > 0]


class DuplicateStore:
    def __init__(self, database: Path):
        self._database = database
        with self._session() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS file_hash ("
                "path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, file_size INTEGER NOT NULL, sha256 TEXT NOT NULL)"
            )
            connection.execute("CREATE TABLE IF NOT EXISTS ignored_groups (sha256 TEXT PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS ignored_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL)"
            )

    @contextmanager
    def _session(self):
        connection = sqlite3.connect(self._database, timeout=30)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def cached_hashes(self, items: Iterable[MediaItem]) -> dict[str, str]:
        """path -> sha256 for the items whose stored hash is still valid (same mtime and size)."""
        wanted = {str(item.path): (item.mtime_ns, item.file_size) for item in items}
        found: dict[str, str] = {}
        try:
            with self._session() as connection:
                for path, mtime_ns, file_size, sha in connection.execute(
                    "SELECT path, mtime_ns, file_size, sha256 FROM file_hash"
                ):
                    if wanted.get(path) == (mtime_ns, file_size):
                        found[path] = sha
        except sqlite3.Error:
            return {}
        return found

    def save_hashes(self, rows: list[tuple[str, int, int, str]]) -> None:
        if not rows:
            return
        try:
            with self._session() as connection:
                connection.executemany("INSERT OR REPLACE INTO file_hash VALUES (?,?,?,?)", rows)
        except sqlite3.Error:
            pass

    def prune(self, present: set[str]) -> None:
        """Forget hashes of files that are no longer in the library."""
        try:
            with self._session() as connection:
                stale = [
                    (path,) for (path,) in connection.execute("SELECT path FROM file_hash") if path not in present
                ]
                if stale:
                    connection.executemany("DELETE FROM file_hash WHERE path = ?", stale)
        except sqlite3.Error:
            pass

    # // "not a duplicate"

    def ignored(self) -> tuple[set[str], dict[str, str]]:
        """(hashes of ignored groups, {path: hash} of ignored single files)."""
        try:
            with self._session() as connection:
                groups = {row[0] for row in connection.execute("SELECT sha256 FROM ignored_groups")}
                files = dict(connection.execute("SELECT path, sha256 FROM ignored_files").fetchall())
        except sqlite3.Error:
            return set(), {}
        return groups, files

    def ignored_count(self) -> int:
        groups, files = self.ignored()
        return len(groups) + len(files)

    def ignore_group(self, sha256: str) -> None:
        self._write("INSERT OR IGNORE INTO ignored_groups VALUES (?)", (sha256,))

    def unignore_group(self, sha256: str) -> None:
        self._write("DELETE FROM ignored_groups WHERE sha256 = ?", (sha256,))

    def ignore_file(self, path: str, sha256: str) -> None:
        self._write("INSERT OR REPLACE INTO ignored_files VALUES (?,?)", (path, sha256))

    def unignore_file(self, path: str) -> None:
        self._write("DELETE FROM ignored_files WHERE path = ?", (path,))

    def reset_ignored(self) -> None:
        self._write("DELETE FROM ignored_groups")
        self._write("DELETE FROM ignored_files")

    def _write(self, sql: str, parameters: tuple = ()) -> None:
        try:
            with self._session() as connection:
                connection.execute(sql, parameters)
        except sqlite3.Error:
            pass


def build_result(
    items: Iterable[MediaItem],
    hashes: dict[str, str],
    ignored_groups: set[str] = frozenset(),
    ignored_files: dict[str, str] | None = None,
) -> DuplicateResult:
    """Group the items that share a hash, leaving out what the person marked "not a duplicate"."""
    ignored_files = ignored_files or {}
    by_hash: dict[str, list[MediaItem]] = defaultdict(list)
    for item in _candidates(items):
        key = str(item.path)
        sha = hashes.get(key)
        if sha is None or sha in ignored_groups or ignored_files.get(key) == sha:
            continue
        by_hash[sha].append(item)
    groups = [
        DuplicateGroup(sha, tuple(sorted(members, key=keep_order)))
        for sha, members in by_hash.items()
        if len(members) > 1
    ]
    groups.sort(key=lambda group: (-group.freeable, group.sha256))
    return DuplicateResult(tuple(groups))


def cached_result(items: list[MediaItem], store: DuplicateStore) -> DuplicateResult:
    """What the last scan found, without reading any file (photos never hashed are simply not in it)."""
    candidates = _candidates(items)
    if not candidates:
        return DuplicateResult()
    groups, files = store.ignored()
    return build_result(candidates, store.cached_hashes(candidates), groups, files)


def hash_file(path: Path, should_stop: Callable[[], bool] = lambda: False) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            while True:
                if should_stop():
                    return None
                chunk = stream.read(READ_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def scan(
    items: list[MediaItem],
    store: DuplicateStore,
    should_stop: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] | None = None,
) -> DuplicateResult | None:
    """Hash the photos that could be copies and return the groups; None when stopped part-way."""
    candidates = _candidates(items)
    by_size: dict[int, list[MediaItem]] = defaultdict(list)
    for item in candidates:
        by_size[item.file_size].append(item)
    possible = [item for members in by_size.values() if len(members) > 1 for item in members]

    hashes = store.cached_hashes(possible)
    todo = [item for item in possible if str(item.path) not in hashes]
    total = len(todo)
    pending: list[tuple[str, int, int, str]] = []
    for done, item in enumerate(todo, 1):
        if should_stop():
            store.save_hashes(pending)
            return None
        sha = hash_file(item.path, should_stop)
        if sha is None:
            if should_stop():
                store.save_hashes(pending)
                return None
            continue  # unreadable right now: leave it out
        key = str(item.path)
        hashes[key] = sha
        pending.append((key, item.mtime_ns, item.file_size, sha))
        if len(pending) >= SAVE_EVERY:
            store.save_hashes(pending)
            pending = []
        if progress is not None:
            progress(done, total)
    store.save_hashes(pending)
    if candidates:
        store.prune({str(item.path) for item in items})
    groups, files = store.ignored()
    return build_result(candidates, hashes, groups, files)
