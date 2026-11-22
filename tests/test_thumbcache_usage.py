import tempfile
import unittest
from pathlib import Path

from npigallery.services.thumbcache import ThumbnailCache


class ThumbnailCacheUsageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = ThumbnailCache(Path(self._tmp.name) / "thumbs.db")
        self.addCleanup(self.cache.close)

    def test_empty_cache(self):
        self.assertEqual(self.cache.usage(), (0, 0))

    def test_counts_thumbnails_and_their_bytes(self):
        self.cache.put("a.jpg", 1, 10, b"x" * 100)
        self.cache.put("b.jpg", 1, 10, b"y" * 250)
        self.assertEqual(self.cache.usage(), (2, 350))

    def test_replacing_a_thumbnail_does_not_double_count(self):
        self.cache.put("a.jpg", 1, 10, b"x" * 100)
        self.cache.put("a.jpg", 2, 10, b"x" * 40)
        self.assertEqual(self.cache.usage(), (1, 40))

    def test_clear_resets_usage(self):
        self.cache.put("a.jpg", 1, 10, b"x" * 100)
        self.cache.clear()
        self.assertEqual(self.cache.usage(), (0, 0))


if __name__ == "__main__":
    unittest.main()