import importlib.util
import io
import os
import sys
import unittest
from collections import OrderedDict
from pathlib import Path

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
HAS_QT = importlib.util.find_spec("PySide6") is not None


def _load_module(name: str, relative: str):
    """Load a Qt-free module straight from its file, so these tests run without PySide6."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


policy = _load_module("viewer_cachepolicy", "npigallery/gui/viewer/cachepolicy.py")
sys.path.insert(0, str(ROOT))
from npigallery.npi.imaging import load_oriented_display, load_oriented_rgba, pixel_buffer  # noqa: E402

MB = 1024 * 1024


class EvictionPolicyTests(unittest.TestCase):
    def test_nothing_dropped_while_within_budget(self):
        sizes = OrderedDict(a=10 * MB, b=10 * MB, c=10 * MB)
        self.assertEqual(policy.eviction_order(sizes, set(), 5, 100 * MB), [])

    def test_drops_least_recent_first_when_over_item_limit(self):
        sizes = OrderedDict((key, MB) for key in "abcdefg")
        self.assertEqual(policy.eviction_order(sizes, set(), 5, 100 * MB), ["a", "b"])

    def test_drops_until_under_byte_limit(self):
        sizes = OrderedDict(a=80 * MB, b=80 * MB, c=80 * MB)
        self.assertEqual(policy.eviction_order(sizes, set(), 5, 200 * MB), ["a"])

    def test_wanted_photos_survive_even_over_budget(self):
        sizes = OrderedDict(a=100 * MB, b=100 * MB, c=100 * MB, d=100 * MB)
        dropped = policy.eviction_order(sizes, {"a", "b", "c"}, 5, 150 * MB)
        self.assertEqual(dropped, ["d"])  # a, b, c are protected; only the unwanted one can go

    def test_empty_cache(self):
        self.assertEqual(policy.eviction_order({}, set(), 5, MB), [])


def _save(image: Image.Image, fmt: str, **options) -> io.BytesIO:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    buffer.seek(0)
    return buffer


def _photo(size=(37, 23)) -> Image.Image:
    image = Image.new("RGB", size)
    image.putdata([(x * 7 % 256, y * 11 % 256, (x + y) * 5 % 256) for y in range(size[1]) for x in range(size[0])])
    return image


class DisplayDecodeTests(unittest.TestCase):
    def assertSamePixels(self, source_factory):
        old = load_oriented_rgba(source_factory())
        new = load_oriented_display(source_factory())
        self.assertEqual(new.size, old.size)
        self.assertEqual(new.convert("RGBA").tobytes(), old.tobytes())
        return new

    def test_opaque_jpeg_is_rgb_not_rgba(self):
        data = _save(_photo(), "JPEG", quality=95).getvalue()
        new = self.assertSamePixels(lambda: io.BytesIO(data))
        self.assertEqual(new.mode, "RGB")

    def test_png_with_alpha_stays_rgba(self):
        image = _photo().convert("RGBA")
        image.putalpha(Image.linear_gradient("L").resize(image.size))
        data = _save(image, "PNG").getvalue()
        new = self.assertSamePixels(lambda: io.BytesIO(data))
        self.assertEqual(new.mode, "RGBA")

    def test_palette_with_transparency_becomes_rgba(self):
        image = _photo().convert("P")
        data = _save(image, "PNG", transparency=0).getvalue()
        new = self.assertSamePixels(lambda: io.BytesIO(data))
        self.assertEqual(new.mode, "RGBA")

    def test_grayscale_becomes_rgb(self):
        data = _save(_photo().convert("L"), "PNG").getvalue()
        new = self.assertSamePixels(lambda: io.BytesIO(data))
        self.assertEqual(new.mode, "RGB")

    def test_exif_orientation_is_applied_like_before(self):
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                exif = Image.Exif()
                exif[0x0112] = orientation
                data = _save(_photo(), "JPEG", quality=95, exif=exif).getvalue()
                new = self.assertSamePixels(lambda: io.BytesIO(data))
                expected = (23, 37) if orientation in (5, 6, 7, 8) else (37, 23)
                self.assertEqual(new.size, expected)


class PixelBufferTests(unittest.TestCase):
    def test_matches_tobytes_for_every_band_layout(self):
        # 3000 px wide RGB rows make a band of a few hundred rows, so the loop crosses a band edge
        for size in ((1, 1), (5, 3), (37, 23), (3000, 1500)):
            for mode in ("RGB", "RGBA"):
                with self.subTest(size=size, mode=mode):
                    image = Image.effect_noise(size, 64).convert(mode)
                    self.assertEqual(bytes(pixel_buffer(image)), image.tobytes())


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class QtMemoryTests(unittest.TestCase):
    def test_qimage_from_display_keeps_pixels_for_odd_widths(self):
        from PySide6.QtGui import QImage

        from npigallery.gui.common.imageconv import qimage_from_display

        for width in (1, 5, 37, 64):  # widths whose RGB rows are not a multiple of 4 bytes
            with self.subTest(width=width):
                source = _photo((width, 9))
                expected = source.tobytes()
                image = qimage_from_display(source)
                self.assertEqual(image.format(), QImage.Format.Format_RGB888)
                self.assertEqual((image.width(), image.height()), (width, 9))
                rows = b"".join(bytes(image.constBits())[y * image.bytesPerLine():][: width * 3] for y in range(9))
                self.assertEqual(rows, expected)

    def test_qimage_from_display_rgba_uses_rgba_format(self):
        from PySide6.QtGui import QImage

        from npigallery.gui.common.imageconv import qimage_from_display

        image = qimage_from_display(_photo((8, 4)).convert("RGBA"))
        self.assertEqual(image.format(), QImage.Format.Format_RGBA8888)

    def _view(self, name, size=8):
        from PySide6.QtGui import QImage

        from npigallery.gui.viewer.loader import ViewData

        return ViewData(name, QImage(size, size, QImage.Format.Format_RGB888), size, size, 0, "-", "-", None)

    def test_release_empties_the_cache_and_ignores_late_results(self):
        from npigallery.gui.viewer.loader import ViewerLoader

        loader = ViewerLoader()
        loader._on_loaded("a", self._view("a"))
        self.assertEqual(len(loader._cache), 1)
        loader.shutdown()
        self.assertEqual(len(loader._cache), 0)
        loader._on_loaded("b", self._view("b"))  # a decode that finished after the window closed
        self.assertEqual(len(loader._cache), 0)

    def test_cache_is_trimmed_by_bytes(self):
        from npigallery.gui.viewer import loader as module

        original = module.CACHE_BYTE_LIMIT
        module.CACHE_BYTE_LIMIT = 3 * 100 * 100 * 3 + 1  # room for three 100x100 RGB888 photos
        try:
            instance = module.ViewerLoader()
            for name in "abcde":
                instance._on_loaded(name, self._view(name, 100))
            self.assertEqual(list(instance._cache), ["c", "d", "e"])
        finally:
            module.CACHE_BYTE_LIMIT = original


if __name__ == "__main__":
    unittest.main()