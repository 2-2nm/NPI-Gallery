import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from npigallery.npi import (
    NpiIntegrityError,
    NpiFormatError,
    pack_file,
    read_meta,
    read_record,
    unpack_file,
    verify_npi,
)
from npigallery.services.batch import dump_folder, restore_folder
from npigallery.services.scanner import describe_media, has_media, media_subfolders, scan_folder

TAKEN = "2023:05:06 07:08:09"


def make_jpeg(path: Path, size=(64, 48), taken: str | None = TAKEN, color=(200, 80, 40)) -> None:
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    if taken:
        exif[0x0132] = taken
    image.save(path, "JPEG", exif=exif)


class NpiRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_pack_unpack_is_byte_identical(self):
        source = self.root / "a.jpg"
        make_jpeg(source)
        packed = self.root / "a.npi"
        meta = pack_file(source, packed)
        self.assertEqual((meta.width, meta.height), (64, 48))
        verify_npi(packed)
        out = self.root / "out"
        restored = unpack_file(packed, out)
        self.assertEqual(restored.read_bytes(), source.read_bytes())
        self.assertEqual(restored.stat().st_mtime_ns, source.stat().st_mtime_ns)

    def test_corruption_is_detected(self):
        source = self.root / "a.jpg"
        make_jpeg(source)
        packed = self.root / "a.npi"
        pack_file(source, packed)
        raw = bytearray(packed.read_bytes())
        raw[-5] ^= 0xFF
        packed.write_bytes(bytes(raw))
        with self.assertRaises(NpiIntegrityError):
            verify_npi(packed)

    def test_truncated_file_is_rejected(self):
        source = self.root / "a.jpg"
        make_jpeg(source)
        packed = self.root / "a.npi"
        pack_file(source, packed)
        packed.write_bytes(packed.read_bytes()[:-10])
        with self.assertRaises(NpiFormatError):
            read_record(packed)

    def test_orientation_swaps_dimensions(self):
        source = self.root / "rot.jpg"
        image = Image.new("RGB", (80, 40), (1, 2, 3))
        exif = Image.Exif()
        exif[0x0112] = 6
        image.save(source, "JPEG", exif=exif)
        meta = pack_file(source, self.root / "rot.npi")
        self.assertEqual((meta.width, meta.height), (40, 80))


class Batch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sub").mkdir()
        make_jpeg(self.root / "one.jpg")
        make_jpeg(self.root / "sub" / "two.jpg", color=(10, 200, 10))
        Image.new("RGB", (10, 10)).save(self.root / "sub" / "three.png")

    def tearDown(self):
        self._tmp.cleanup()

    def test_dump_is_idempotent(self):
        first = dump_folder(self.root)
        self.assertEqual((first.processed, first.skipped, len(first.failures)), (3, 0, 0))
        second = dump_folder(self.root)
        self.assertEqual((second.processed, second.skipped), (0, 3))

    def test_same_stem_different_extension_does_not_collide(self):
        make_jpeg(self.root / "sub" / "three.jpg")
        result = dump_folder(self.root)
        self.assertEqual(len(result.failures), 0)
        names = sorted(path.name for path in (self.root / "sub").glob("*.npi"))
        self.assertEqual(len(names), 3)
        originals = {read_meta(self.root / "sub" / name).original_name for name in names}
        self.assertEqual(originals, {"two.jpg", "three.png", "three.jpg"})

    def test_dump_to_other_folder_then_restore(self):
        packed = self.root / "packed"
        restored = self.root / "restored"
        self.assertEqual(len(dump_folder(self.root, packed).failures), 0)
        result = restore_folder(packed, restored)
        self.assertEqual((result.processed, len(result.failures)), (3, 0))
        self.assertEqual(
            (restored / "sub" / "two.jpg").read_bytes(), (self.root / "sub" / "two.jpg").read_bytes()
        )
        again = restore_folder(packed, restored)
        self.assertEqual((again.processed, again.skipped), (0, 3))

    def test_stats_callback_reports_live_counts(self):
        seen = []
        dump_folder(self.root, on_stats=lambda r: seen.append((r.processed, r.skipped, len(r.failures))))
        self.assertEqual(seen, [(1, 0, 0), (2, 0, 0), (3, 0, 0)])
        seen.clear()
        dump_folder(self.root, on_stats=lambda r: seen.append((r.processed, r.skipped, len(r.failures))))
        self.assertEqual(seen[-1], (0, 3, 0))

    def test_cancel(self):
        result = dump_folder(self.root, should_cancel=lambda: True)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.processed, 0)


class Scanner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sort_time_is_same_before_and_after_packing(self):
        source = self.root / "a.jpg"
        make_jpeg(source)
        os.utime(source, (1_000_000_000, 1_000_000_000))  # mtime far from the EXIF date
        original = describe_media(source)
        packed = self.root / "a.npi"
        pack_file(source, packed)
        item = describe_media(packed)
        self.assertEqual(original.sort_time, item.sort_time)
        self.assertNotEqual(original.sort_time, 1_000_000_000)

    def test_without_exif_falls_back_to_mtime(self):
        source = self.root / "plain.jpg"
        make_jpeg(source, taken=None)
        os.utime(source, (1_500_000_000, 1_500_000_000))
        self.assertEqual(describe_media(source).sort_time, 1_500_000_000)

    def test_scan_orders_newest_first_and_skips_broken(self):
        old, new = self.root / "old.jpg", self.root / "new.jpg"
        make_jpeg(old, taken="2001:01:01 00:00:00")
        make_jpeg(new, taken="2020:01:01 00:00:00")
        (self.root / "broken.jpg").write_bytes(b"not an image")
        names = [item.name for item in scan_folder(self.root)]
        self.assertEqual(names[:2], ["new.jpg", "old.jpg"])


class SubfolderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_only_folders_with_photos_inside_are_listed(self):
        (self.root / "empty").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.txt").write_text("x")
        (self.root / ".hidden").mkdir()
        make_jpeg(self.root / ".hidden" / "h.jpg", taken=None)
        (self.root / "direct").mkdir()
        make_jpeg(self.root / "direct" / "d.jpg", taken=None)
        (self.root / "deep" / "a" / "b").mkdir(parents=True)
        make_jpeg(self.root / "deep" / "a" / "b" / "p.jpg", taken=None)
        names = [folder.name for folder in media_subfolders(self.root)]
        self.assertEqual(names, ["deep", "direct"])

    def test_leaf_folder_has_no_subfolders(self):
        (self.root / "leaf").mkdir()
        make_jpeg(self.root / "leaf" / "p.jpg", taken=None)
        self.assertTrue(has_media(self.root / "leaf"))
        self.assertEqual(media_subfolders(self.root / "leaf"), [])

    def test_missing_folder_is_safe(self):
        self.assertEqual(media_subfolders(self.root / "nope"), [])
        self.assertFalse(has_media(self.root / "nope"))


if __name__ == "__main__":
    unittest.main()
