import importlib.util
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path, PurePosixPath

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HAS_QT = importlib.util.find_spec("PySide6") is not None

from npigallery.filesystem import is_under, root_keys  # noqa: E402
from npigallery.services.index import MediaIndex  # noqa: E402
from npigallery.services.scanner import MediaItem, scan_folders  # noqa: E402


class IsUnderTests(unittest.TestCase):
    def test_matches_path_is_relative_to(self):
        roots = ["/photos", "/photos/trips/2024", "/mnt/usb", "/"]
        cases = [
            "/photos", "/photos/a.jpg", "/photos/trips/2024/b.jpg", "/photosX/a.jpg", "/photo/a.jpg",
            "/photos2", "/mnt/usb/x/y.png", "/mnt/usbstick/x.png", "/elsewhere/c.jpg", "/mnt", "/",
        ]
        for root in roots:
            keys = root_keys([root])
            for case in cases:
                with self.subTest(root=root, path=case):
                    self.assertEqual(is_under(case, keys), PurePosixPath(case).is_relative_to(root))

    def test_random_paths_agree_with_pathlib(self):
        rng = random.Random(7)
        parts = ["a", "ab", "abc", "photos", "photosx", "2024", "x"]
        for _ in range(500):
            root = "/" + "/".join(rng.choice(parts) for _ in range(rng.randint(1, 3)))
            path = "/" + "/".join(rng.choice(parts) for _ in range(rng.randint(1, 5)))
            self.assertEqual(is_under(path, root_keys([root])), PurePosixPath(path).is_relative_to(root), (root, path))

    def test_any_of_several_roots(self):
        keys = root_keys(["/a", "/b/c"])
        self.assertTrue(is_under("/b/c/d.jpg", keys))
        self.assertFalse(is_under("/b/x.jpg", keys))
        self.assertFalse(is_under("/anything", ()))


def _item(path: Path, mtime: int = 1, size: int = 100) -> MediaItem:
    return MediaItem(path, path.name, False, size, 1.0e9 + mtime, "JPG", 640, 480, mtime, size, None, None)


class MediaIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.db = self.base / "index.db"
        self.index = MediaIndex(self.db)
        self.root_a = self.base / "a"
        self.root_b = self.base / "b"
        self.a_items = [_item(self.root_a / f"p{n}.jpg", mtime=n) for n in range(5)]
        self.b_items = [_item(self.root_b / f"q{n}.jpg", mtime=n) for n in range(3)]

    def _rows(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return {row[0]: row[1:] for row in connection.execute("SELECT path, mtime_ns, file_size FROM media")}

    def test_round_trip_and_root_filter(self):
        self.index.save(self.a_items, [self.root_a])
        self.index.save(self.b_items, [self.root_b])
        loaded = self.index.load([self.root_a])
        self.assertEqual(sorted(item.path for item in loaded), sorted(item.path for item in self.a_items))
        self.assertEqual(len(self.index.load([self.root_a, self.root_b])), 8)

    def test_unchanged_library_writes_nothing(self):
        self.index.save(self.a_items, [self.root_a])
        watcher = sqlite3.connect(self.db)
        before = watcher.execute("PRAGMA data_version").fetchone()[0]
        self.index.save(list(self.a_items), [self.root_a])
        self.assertEqual(watcher.execute("PRAGMA data_version").fetchone()[0], before)
        watcher.close()

    def test_changed_photo_is_rewritten_and_removed_photo_is_deleted(self):
        self.index.save(self.a_items, [self.root_a])
        edited = replace(self.a_items[1], mtime_ns=999, file_size=555)
        remaining = [self.a_items[0], edited, self.a_items[2], self.a_items[3]]  # p4 is gone
        self.index.save(remaining, [self.root_a])
        rows = self._rows()
        self.assertNotIn(str(self.a_items[4].path), rows)
        self.assertEqual(rows[str(edited.path)], (999, 555))
        self.assertEqual(len(rows), 4)

    def test_new_photo_is_added(self):
        self.index.save(self.a_items, [self.root_a])
        extra = _item(self.root_a / "new.jpg", mtime=77)
        self.index.save(self.a_items + [extra], [self.root_a])
        self.assertIn(str(extra.path), self._rows())

    def test_rows_of_other_roots_are_left_alone(self):
        self.index.save(self.a_items, [self.root_a])
        self.index.save(self.b_items, [self.root_b])
        self.index.save([], [self.root_a])  # library a emptied
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(str(self.root_b) in key for key in rows))


class ScanIdentityTests(unittest.TestCase):
    """The scan worker skips index / thumbnail housekeeping when every photo is the object it already had."""

    def test_second_scan_returns_the_same_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(40):  # more than 32, so the thread-pool path runs
                Image.new("RGB", (16 + n, 12), (n, 10, 10)).save(root / f"p{n}.png")
            first = scan_folders([root], True)
            known = {str(item.path): item for item in first}
            second = scan_folders([root], True, known=known, workers=2)
            self.assertEqual(len(second), len(first))
            self.assertTrue(all(known[str(item.path)] is item for item in second))

    def test_a_changed_file_breaks_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(3):
                Image.new("RGB", (16, 12), (n, 10, 10)).save(root / f"p{n}.png")
            known = {str(item.path): item for item in scan_folders([root], True)}
            Image.new("RGB", (40, 30), (200, 10, 10)).save(root / "p1.png")
            second = scan_folders([root], True, known=known)
            self.assertFalse(all(known.get(str(item.path)) is item for item in second))


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class LoaderBackgroundQueueTests(unittest.TestCase):
    def test_visible_work_goes_first_and_background_fills_free_workers(self):
        from npigallery.gui.gallery.thumbnails import ThumbnailLoader
        from npigallery.services.thumbcache import ThumbnailCache

        class Pool:
            def __init__(self):
                self.started = []

            def start(self, task):
                self.started.append(str(task._item.path))

            def setMaxThreadCount(self, _count):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            cache = ThumbnailCache(Path(tmp) / "t.db")
            loader = ThumbnailLoader(cache)
            pool = Pool()
            loader._pool = pool
            loader._workers = 0  # nothing starts while the lists are filled
            items = {name: _item(Path(tmp) / f"{name}.jpg") for name in "abcdef"}
            loader.set_background([items["d"], items["e"], items["f"], items["a"]])
            loader.set_queue([items["a"], items["b"]])
            loader._workers = 10
            loader._pump()
            # visible a, b first; then the background ones, and `a` is not decoded twice
            self.assertEqual([Path(p).stem for p in pool.started], ["a", "b", "d", "e", "f"])
            loader.shutdown = lambda: None
            cache.close()


if __name__ == "__main__":
    unittest.main()