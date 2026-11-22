import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

try:
    from npigallery.gui.backdrop import mica_render, system

    HAS_BACKDROP = True
except Exception:  # PySide6 missing: the package __init__ imports Qt
    HAS_BACKDROP = False


@unittest.skipUnless(HAS_BACKDROP, "backdrop package needs PySide6")
class StyleTests(unittest.TestCase):
    def test_registry_values_map_to_the_desktop_fit_styles(self):
        cases = {("10", "0"): "fill", ("6", "0"): "fit", ("2", "0"): "stretch", ("0", "0"): "center", ("22", "0"): "span"}
        for (style, tile), expected in cases.items():
            self.assertEqual(mica_render.style_from_registry(style, tile), expected)

    def test_tile_flag_wins_over_the_style_value(self):
        self.assertEqual(mica_render.style_from_registry("0", "1"), "tile")
        self.assertEqual(mica_render.style_from_registry("10", "1"), "tile")

    def test_unknown_style_is_fill(self):
        self.assertEqual(mica_render.style_from_registry("", "0"), "fill")
        self.assertEqual(mica_render.style_from_registry("99", "0"), "fill")

    def test_blur_radius_is_clamped_and_grows_with_the_setting(self):
        self.assertEqual(mica_render.blur_radius(-5), 0)
        self.assertEqual(mica_render.blur_radius(0), 0)
        self.assertLess(mica_render.blur_radius(8), mica_render.blur_radius(16))
        self.assertEqual(mica_render.blur_radius(500), mica_render.blur_radius(mica_render.MAX_MICA_BLUR))


@unittest.skipUnless(HAS_BACKDROP, "backdrop package needs PySide6")
class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "wide.png")
        Image.new("RGB", (400, 100), (200, 40, 40)).save(self.path)  # four times as wide as it is tall

    def test_picture_is_about_the_working_width_and_keeps_the_screen_shape(self):
        out = mica_render.render(self.path, "fill", (1920, 1200), (0, 0, 1, 1), (0, 0, 0), 8)
        self.assertEqual(out.size, (mica_render.WORK_WIDTH, round(1200 * mica_render.WORK_WIDTH / 1920)))
        self.assertEqual(out.mode, "RGB")

    def test_fill_covers_the_screen_with_the_picture(self):
        out = mica_render.render(self.path, "fill", (1920, 1200), (0, 0, 1, 1), (0, 0, 255), 0)
        self.assertEqual(out.getpixel((2, 2)), (200, 40, 40))  # no desktop-colour border

    def test_fit_leaves_the_desktop_colour_around_the_picture(self):
        out = mica_render.render(self.path, "fit", (1920, 1200), (0, 0, 1, 1), (0, 0, 255), 0)
        self.assertEqual(out.getpixel((out.width // 2, 2)), (0, 0, 255))  # letterbox above
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2)), (200, 40, 40))

    def test_missing_or_empty_path_gives_the_plain_desktop_colour(self):
        for path in ("", str(Path(self._tmp.name) / "gone.png")):
            out = mica_render.render(path, "fill", (1920, 1200), (0, 0, 1, 1), (7, 8, 9), 12)
            self.assertEqual(out.getpixel((5, 5)), (7, 8, 9))

    def test_blur_softens_an_edge(self):
        edge = Path(self._tmp.name) / "edge.png"
        picture = Image.new("RGB", (640, 400), (0, 0, 0))
        picture.paste((255, 255, 255), (320, 0, 640, 400))
        picture.save(edge)
        sharp = mica_render.render(str(edge), "stretch", (640, 400), (0, 0, 1, 1), (0, 0, 0), 0)
        soft = mica_render.render(str(edge), "stretch", (640, 400), (0, 0, 1, 1), (0, 0, 0), 12)
        self.assertEqual(sharp.getpixel((310, 200)), (0, 0, 0))
        self.assertGreater(soft.getpixel((310, 200))[0], 0)

    def test_span_region_picks_one_screens_share_of_the_desktop(self):
        right = mica_render.render(self.path, "span", (3840, 1200), (0.5, 0, 0.5, 1), (0, 0, 0), 0)
        self.assertEqual(right.size, (mica_render.WORK_WIDTH // 2, round(1200 * mica_render.WORK_WIDTH / 3840)))

    def test_saturation_adds_colour_and_leaves_grey_alone(self):
        plain = mica_render.render(self.path, "stretch", (640, 400), (0, 0, 1, 1), (0, 0, 0), 0)
        richer = mica_render.render(self.path, "stretch", (640, 400), (0, 0, 1, 1), (0, 0, 0), 0, mica_render.ACRYLIC_SATURATION)
        spread = lambda pixel: max(pixel) - min(pixel)  # noqa: E731 - how far from grey a pixel is
        self.assertGreater(spread(richer.getpixel((5, 5))), spread(plain.getpixel((5, 5))))
        grey = Path(self._tmp.name) / "grey.png"
        Image.new("RGB", (64, 64), (90, 90, 90)).save(grey)
        flat = mica_render.render(str(grey), "stretch", (640, 400), (0, 0, 1, 1), (0, 0, 0), 4, mica_render.ACRYLIC_SATURATION)
        self.assertEqual(spread(flat.getpixel((5, 5))), 0)

    def test_every_style_renders(self):
        for style in ("fill", "fit", "stretch", "center", "tile", "span"):
            out = mica_render.render(self.path, style, (1920, 1200), (0, 0, 1, 1), (1, 2, 3), 4)
            self.assertEqual(out.size[0], mica_render.WORK_WIDTH, style)


@unittest.skipUnless(HAS_BACKDROP, "backdrop package needs PySide6")
class NoiseTests(unittest.TestCase):
    def test_grain_tile_is_a_square_of_grey_that_is_not_flat(self):
        tile = mica_render.noise_tile()
        self.assertEqual(tile.mode, "L")
        self.assertEqual(tile.size, (mica_render.NOISE_SIZE, mica_render.NOISE_SIZE))
        low, high = tile.getextrema()
        self.assertLess(low, 100)
        self.assertGreater(high, 150)


@unittest.skipUnless(HAS_BACKDROP, "backdrop package needs PySide6")
class ClipTests(unittest.TestCase):
    def test_a_piece_inside_the_picture_is_unchanged(self):
        source, target = (10, 10, 100, 50), (0, 0, 200, 100)
        self.assertEqual(mica_render.clip_source(source, target, (0, 0, 1920, 1200)), (source, target))

    def test_the_target_loses_the_same_share_as_the_source(self):
        clipped = mica_render.clip_source((-20, 0, 100, 50), (0, 0, 200, 100), (0, 0, 1920, 1200))
        self.assertEqual(clipped, ((0, 0, 80, 50), (40.0, 0.0, 160.0, 100.0)))

    def test_a_piece_past_the_far_edges_is_cut_there(self):
        clipped = mica_render.clip_source((1900, 1180, 100, 50), (0, 0, 100, 50), (0, 0, 1920, 1200))
        self.assertEqual(clipped, ((1900, 1180, 20, 20), (0.0, 0.0, 20.0, 20.0)))

    def test_nothing_is_left_when_it_lies_outside(self):
        self.assertIsNone(mica_render.clip_source((3000, 0, 100, 50), (0, 0, 100, 50), (0, 0, 1920, 1200)))
        self.assertIsNone(mica_render.clip_source((0, 0, 0, 0), (0, 0, 10, 10), (0, 0, 10, 10)))


@unittest.skipUnless(HAS_BACKDROP, "backdrop package needs PySide6")
@unittest.skipIf(sys.platform == "win32", "these are the answers off Windows")
class OffWindowsTests(unittest.TestCase):
    def test_system_effects_are_unavailable_and_harmless(self):
        self.assertFalse(system.translucent_supported())
        self.assertFalse(system.acrylic_available())
        self.assertFalse(system.transparency_enabled())
        self.assertIsNone(system.desktop_wallpaper())
        self.assertFalse(system.apply_system_acrylic(0, True))  # nothing could be switched on
        self.assertTrue(system.apply_system_acrylic(0, False))  # switching off is always fine


if __name__ == "__main__":
    unittest.main()