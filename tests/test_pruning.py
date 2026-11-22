import tempfile
import unittest
from pathlib import Path

from npigallery.services.pruning import missing_favorites
from npigallery.services.thumbcache import ThumbnailCache


class PruningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "library"
        self.root.mkdir()
        self.outside = base / "elsewhere"
        self.outside.mkdir()
        self.alive = self.root / "alive.jpg"
        self.alive.write_bytes(b"x")
        self.unscanned = self.root / "unscanned.jpg"  # on disk, but the scan missed it
        self.unscanned.write_bytes(b"x")
        self.gone = self.root / "gone.jpg"  # never created: deleted behind the app's back
        self.other = self.outside / "other.jpg"  # not in the library at all
        self.other.write_bytes(b"x")
        self.unplugged = base / "drive" / "photo.jpg"  # its library folder does not exist

    def test_favorites_only_dropped_when_really_gone(self):
        favorites = {str(p) for p in (self.alive, self.unscanned, self.gone, self.other, self.unplugged)}
        present = {str(self.alive)}
        gone = missing_favorites(favorites, present, [self.root, self.unplugged.parent])
        self.assertEqual(gone, {str(self.gone)})

    def test_favorites_untouched_when_no_library_folder_is_reachable(self):
        gone = missing_favorites({str(self.gone)}, set(), [self.root / "missing-root"])
        self.assertEqual(gone, set())

    def test_thumbnail_cache_prune(self):
        cache = ThumbnailCache(Path(self._tmp.name) / "thumbs.db")
        self.addCleanup(cache.close)
        for path in (self.alive, self.unscanned, self.gone, self.other, self.unplugged):
            cache.put(str(path), 1, 1, b"thumb")
        removed = cache.prune([self.root, self.unplugged.parent], {str(self.alive)})
        self.assertEqual(removed, 1)
        self.assertIsNone(cache.get(str(self.gone), 1, 1))
        for kept in (self.alive, self.unscanned, self.other, self.unplugged):
            self.assertIsNotNone(cache.get(str(kept), 1, 1), kept)

    def test_thumbnail_cache_prune_skips_unreachable_roots(self):
        cache = ThumbnailCache(Path(self._tmp.name) / "thumbs.db")
        self.addCleanup(cache.close)
        cache.put(str(self.gone), 1, 1, b"thumb")
        self.assertEqual(cache.prune([self.root / "missing-root"], set()), 0)
        self.assertIsNotNone(cache.get(str(self.gone), 1, 1))


if __name__ == "__main__":
    unittest.main()