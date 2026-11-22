import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from npigallery.services import wallpaper


class PrepareImageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cache = self.root / "cache"
        patcher = mock.patch.object(wallpaper, "cache_directory", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_plain_png_and_jpeg_are_used_as_they_are(self):
        for name, kind in (("a.png", "PNG"), ("b.jpg", "JPEG"), ("c.bmp", "BMP")):
            path = self.root / name
            Image.new("RGB", (40, 30), (10, 20, 30)).save(path, kind)
            self.assertEqual(wallpaper.prepare_image(path), path)

    def test_other_formats_become_a_png_in_the_cache(self):
        path = self.root / "d.webp"
        Image.new("RGB", (40, 30), (200, 10, 30)).save(path, "WEBP")
        ready = wallpaper.prepare_image(path)
        self.assertEqual(ready.suffix, ".png")
        self.assertEqual(ready.parent, self.cache / "wallpaper")
        with Image.open(ready) as image:
            self.assertEqual(image.size, (40, 30))
        self.assertEqual(wallpaper.prepare_image(path), ready)  # cached

    def test_jpeg_with_exif_orientation_is_turned_upright(self):
        path = self.root / "e.jpg"
        image = Image.new("RGB", (40, 20), (1, 2, 3))
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 clockwise to display
        image.save(path, "JPEG", exif=exif)
        ready = wallpaper.prepare_image(path)
        self.assertNotEqual(ready, path)
        with Image.open(ready) as result:
            self.assertEqual(result.size, (20, 40))

    def test_unreadable_file_raises_wallpaper_error(self):
        path = self.root / "broken.webp"
        path.write_bytes(b"not an image")
        with self.assertRaises(wallpaper.WallpaperError):
            wallpaper.prepare_image(path)

    def test_not_windows_is_refused(self):
        with mock.patch.object(wallpaper.sys, "platform", "linux"):
            with self.assertRaises(wallpaper.WallpaperError):
                wallpaper.set_desktop_wallpaper(self.root / "x.png")


if __name__ == "__main__":
    unittest.main()
