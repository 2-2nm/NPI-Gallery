import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from npigallery.npi import pack_file, read_record, verify_npi  # noqa: E402
from npigallery.services import editing as ed  # noqa: E402

SIZE = (400.0, 300.0)


class RectGeometryTests(unittest.TestCase):
    def test_largest_rect_is_valid_and_fills_the_picture_when_straight(self):
        self.assertEqual(ed.largest_rect(SIZE, 0, None), (0.0, 0.0, 400.0, 300.0))
        rect = ed.largest_rect(SIZE, 10, None)
        self.assertTrue(ed.rect_valid(rect, SIZE, 10))
        self.assertFalse(ed.rect_valid((0, 0, 400, 300), SIZE, 10))

    def test_largest_rect_keeps_the_requested_shape(self):
        x, y, w, h = ed.largest_rect(SIZE, 7, 1.0)
        self.assertAlmostEqual(w, h)

    def test_approach_stops_at_the_edge(self):
        rect = ed.largest_rect(SIZE, 10, None)
        moved = ed.approach(rect, (rect[0] - 500, rect[1], rect[2], rect[3]), SIZE, 10)
        self.assertTrue(ed.rect_valid(moved, SIZE, 10))
        self.assertLess(moved[0], rect[0] + 1e-9)

    def test_fit_rect_recentres_a_rect_that_left_the_picture(self):
        fitted = ed.fit_rect((390, 290, 100, 100), SIZE, 30)
        self.assertTrue(ed.rect_valid(fitted, SIZE, 30))


class StateTests(unittest.TestCase):
    def test_identity(self):
        self.assertTrue(ed.EditState().is_identity(SIZE))
        self.assertTrue(ed.EditState(ops=("cw", "ccw")).is_identity(SIZE))
        self.assertFalse(ed.EditState(ops=("cw",)).is_identity(SIZE))
        self.assertFalse(ed.EditState(angle=1).is_identity(SIZE))
        self.assertTrue(ed.EditState(crop=(0, 0, 400, 300)).is_identity(SIZE))

    def test_crop_follows_the_pixels_through_turns_and_flips(self):
        image = Image.new("RGB", (400, 300))
        image.putpixel((50, 20), (255, 0, 0))
        state = ed.EditState(crop=(40, 10, 100, 60))
        for op in ("cw", "fh", "ccw", "fv"):
            state = ed.op_state(state, ed.working_size((400, 300), state.ops), op)
        work = ed.apply_ops(image, state.ops)
        red = next((i, j) for i in range(work.width) for j in range(work.height) if work.getpixel((i, j)) == (255, 0, 0))
        x, y, w, h = state.crop
        self.assertTrue(x <= red[0] < x + w and y <= red[1] < y + h)

    def test_flip_mirrors_the_tilt(self):
        state = ed.op_state(ed.EditState(angle=5), SIZE, "fh")
        self.assertEqual(state.angle, -5)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.jpg = self.folder / "a.jpg"
        Image.new("RGB", (400, 300), (10, 200, 30)).save(self.jpg)

    def test_render_size_and_straightened_crop(self):
        rect = ed.largest_rect(SIZE, 5, 1.0)
        out = ed.render(Image.open(self.jpg), ed.EditState(angle=5, crop=rect))
        self.assertEqual(out.size, (round(rect[2]), round(rect[3])))
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2)), (10, 200, 30))  # no empty corners
        self.assertEqual(out.getpixel((0, 0)), (10, 200, 30))

    def test_copy_leaves_the_original_and_overwrite_replaces_it(self):
        state = ed.EditState(crop=(0, 0, 200, 100))
        copy = ed.save_edit(self.jpg, state, True)
        self.assertNotEqual(copy, self.jpg)
        self.assertEqual(Image.open(copy).size, (200, 100))
        self.assertEqual(Image.open(self.jpg).size, (400, 300))
        ed.save_edit(self.jpg, state, False)
        self.assertEqual(Image.open(self.jpg).size, (200, 100))

    def test_npi_container_is_rebuilt(self):
        npi = self.folder / "a.npi"
        pack_file(self.jpg, npi)
        state = ed.EditState(ops=("cw",), crop=(0, 0, 300, 200))
        copy = ed.save_edit(npi, state, True)
        meta = read_record(copy).meta
        self.assertEqual((meta.width, meta.height), (300, 200))
        verify_npi(copy)
        self.assertEqual(read_record(npi).meta.width, 400)

    def test_preview_is_downscaled_but_reports_the_real_size(self):
        big = self.folder / "big.png"
        Image.new("RGB", (3000, 2000)).save(big)
        preview, full = ed.load_for_edit(big, max_side=1000)
        self.assertEqual(full, (3000, 2000))
        self.assertLessEqual(max(preview.size), 1000)


if __name__ == "__main__":
    unittest.main()
