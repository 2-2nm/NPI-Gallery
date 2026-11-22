import random
import unittest

from npigallery.gui.gallery.layout import justify_rows

WIDTH, GAP, TARGET = 1000, 4, 160


class JustifyRows(unittest.TestCase):
    def check(self, aspects):
        rows = justify_rows(aspects, WIDTH, TARGET, GAP)
        seen = [index for _height, tiles in rows for index, _x, _w in tiles]
        self.assertEqual(seen, list(range(len(aspects))))
        for position, (height, tiles) in enumerate(rows):
            self.assertGreater(height, 0)
            previous_right = None
            for _index, x, width in tiles:
                self.assertGreaterEqual(x, 0)
                self.assertGreater(width, 0)
                if previous_right is not None:
                    self.assertEqual(x - previous_right, GAP)
                previous_right = x + width
            if position < len(rows) - 1:
                # every full row spans the whole width
                self.assertEqual(previous_right, WIDTH)
            else:
                self.assertLessEqual(previous_right, WIDTH)
        return rows

    def test_empty(self):
        self.assertEqual(justify_rows([], WIDTH, TARGET, GAP), [])

    def test_single_wide_item_is_clamped_to_width(self):
        rows = self.check([5.0])
        self.assertEqual(len(rows), 1)

    def test_last_row_keeps_target_height(self):
        rows = self.check([1.5] * 7)
        self.assertEqual(rows[-1][0], TARGET)

    def test_random_aspects(self):
        rng = random.Random(7)
        for _ in range(200):
            self.check([rng.uniform(0.2, 5.0) for _ in range(rng.randint(1, 60))])

    def test_row_heights_stay_near_target(self):
        rng = random.Random(3)
        aspects = [rng.choice([0.67, 1.0, 1.33, 1.5, 1.78]) for _ in range(300)]
        rows = justify_rows(aspects, WIDTH, TARGET, GAP)
        for height, _tiles in rows[:-1]:
            self.assertTrue(TARGET * 0.5 < height < TARGET * 1.6, height)


if __name__ == "__main__":
    unittest.main()
