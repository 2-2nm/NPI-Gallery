"""Albums: named, virtual groups of photos and videos.

An album only remembers file paths. It never touches the files on disk, and one album can hold
files from any folder. Like favourites, paths are kept in step by the app when it moves, renames
or deletes a file (see `remap` and `discard`).
"""

import json
from collections.abc import Callable, Iterable


class AlbumStore:
    def __init__(self) -> None:
        self._albums: dict[str, set[str]] = {}

    # reading

    def names(self) -> list[str]:
        return sorted(self._albums, key=str.casefold)

    def exists(self, name: str) -> bool:
        return name in self._albums

    def find(self, name: str) -> str | None:
        """The stored name that equals `name` ignoring case, or None."""
        wanted = name.strip().casefold()
        return next((stored for stored in self._albums if stored.casefold() == wanted), None)

    def paths(self, name: str) -> frozenset[str]:
        return frozenset(self._albums.get(name, ()))

    def all_paths(self) -> set[str]:
        return set().union(*self._albums.values()) if self._albums else set()

    def counts(self, present: set[str]) -> list[tuple[str, int]]:
        """(name, number of its files that are in the library right now), sorted by name."""
        return [(name, len(self._albums[name] & present)) for name in self.names()]

    def unique_name(self, base: str = "New album") -> str:
        if self.find(base) is None:
            return base
        number = 2
        while self.find(f"{base} {number}") is not None:
            number += 1
        return f"{base} {number}"

    # changing; each returns what changed so the caller knows whether to save

    def create(self, name: str, paths: Iterable[str] = ()) -> bool:
        name = name.strip()
        if not name or self.find(name) is not None:
            return False
        self._albums[name] = set(paths)
        return True

    def add(self, name: str, paths: Iterable[str]) -> int:
        """Add files to an album. Returns how many were not in it yet."""
        album = self._albums.get(name)
        if album is None:
            return 0
        before = len(album)
        album.update(paths)
        return len(album) - before

    def remove(self, name: str, paths: Iterable[str]) -> int:
        album = self._albums.get(name)
        if album is None:
            return 0
        before = len(album)
        album.difference_update(paths)
        return before - len(album)

    def rename(self, name: str, new_name: str) -> bool:
        new_name = new_name.strip()
        if name not in self._albums or not new_name:
            return False
        clash = self.find(new_name)
        if clash is not None and clash != name:
            return False
        if new_name == name:
            return False
        self._albums[new_name] = self._albums.pop(name)
        return True

    def delete(self, name: str) -> bool:
        return self._albums.pop(name, None) is not None

    def remap(self, mapping: Callable[[str], str]) -> bool:
        """Replace every stored path by `mapping(path)` (a file or folder was moved or renamed)."""
        changed = False
        for name, album in self._albums.items():
            updated = {mapping(path) for path in album}
            if updated != album:
                self._albums[name] = updated
                changed = True
        return changed

    def discard(self, gone: Callable[[str], bool]) -> bool:
        """Forget every path for which `gone(path)` is true (the file was deleted)."""
        changed = False
        for album in self._albums.values():
            doomed = {path for path in album if gone(path)}
            if doomed:
                album.difference_update(doomed)
                changed = True
        return changed

    # storage

    def to_json(self) -> str:
        return json.dumps({name: sorted(self._albums[name]) for name in self.names()})

    @classmethod
    def from_json(cls, text: str) -> "AlbumStore":
        store = cls()
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return store
        if not isinstance(data, dict):
            return store
        for name, paths in data.items():
            if isinstance(name, str) and isinstance(paths, list):
                store.create(name, (path for path in paths if isinstance(path, str)))
        return store
