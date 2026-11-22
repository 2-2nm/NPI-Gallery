import time
import unittest

from npigallery.gui.gallery.rail import LABEL_GAP, MIN_STOP_GAP, Unit, build_rail


def ts(year, month, day):
    return time.mktime((year, month, day, 12, 0, 0, 0, 0, -1))


def rows_for(days, per_day=1, row_h=100.0, newest_first=True):
    days = sorted(days, reverse=newest_first)
    rows, y = [], 0.0
    for day in days:
        for _ in range(per_day):
            rows.append((y, ts(*day)))
            y += row_h
    return rows, y


class RailTests(unittest.TestCase):
    def test_few_days_get_a_dot_per_day(self):
        rows, height = rows_for([(2026, 11, d) for d in range(1, 11)])
        rail = build_rail(rows, height, span=600)
        self.assertEqual(rail.unit, Unit.DAY)
        self.assertEqual(len(rail.stops), 10)
        self.assertEqual(rail.stops[0].kind, "major")
        self.assertEqual(rail.stops[0].caption, "2026")
        self.assertTrue(all(stop.kind == "minor" for stop in rail.stops[1:]))

    def test_unit_coarsens_when_space_runs_out(self):
        days = [(y, m, 15) for y in range(2010, 2027) for m in range(1, 13)]
        rows, height = rows_for(days)
        rail = build_rail(rows, height, span=700)
        self.assertIn(rail.unit, (Unit.QUARTER, Unit.YEAR))
        self.assertLessEqual(len(rail.stops) * MIN_STOP_GAP, 700)

    def test_every_year_start_is_major_even_for_weeks(self):
        days = [(2025, 12, 29), (2025, 12, 30), (2026, 1, 1), (2026, 1, 2), (2026, 1, 8)]
        rows, height = rows_for(days)
        rail = build_rail(rows, height, span=60 * 12)
        years = [stop.caption for stop in rail.majors]
        self.assertEqual(sorted(years), ["2025", "2026"])

    def test_labels_follow_the_unit(self):
        rows, height = rows_for([(2026, 11, 13), (2026, 11, 12)])
        rail = build_rail(rows, height, span=600)
        self.assertEqual(rail.stops[0].label, "Fri, 13 Nov 2026")
        months = [(2026, m, d) for m in range(1, 13) for d in (5, 20)]
        rows, height = rows_for(months)
        rail = build_rail(rows, height, span=12 * 12)
        self.assertEqual(rail.unit, Unit.MONTH)
        self.assertEqual(rail.stops[0].label, "December 2026")

    def test_stop_at_and_nearest(self):
        days = [(2026, m, 5) for m in range(1, 7)]
        rows, height = rows_for(days)
        rail = build_rail(rows, height - 100, span=600)
        self.assertEqual(rail.stop_at(0.0).row, 0)
        self.assertEqual(rail.stop_at(1.0).row, len(rows) - 1)
        near = rail.nearest(rail.stops[2].frac + 0.001, 0.01)
        self.assertEqual(near.row, 2)
        self.assertIsNone(rail.nearest(rail.stops[2].frac + 0.05, 0.001))

    def test_lumpy_library_still_gets_dots(self):
        # A pile of recent photos plus a trickle of older months must not collapse to years-only.
        rows, y = [], 0.0
        for k in range(60):
            for _ in range(6):
                rows.append((y, ts(2026, 11, 15) - k * 86400))
                y += 160
        for year in range(2021, 2027):
            for month in range(1, 9):
                rows.append((y, ts(year, month, 10)))
                y += 160
        rail = build_rail(rows, y - 860, span=830)
        self.assertNotEqual(rail.unit, Unit.YEAR)
        self.assertGreater(len(rail.stops), len(rail.majors))

    def _old_years_library(self):
        """Lots of recent photos, a trickle of older years: the shape that squeezed old years away."""
        counts = {2026: 900, 2025: 700, 2024: 400, 2023: 300, 2022: 200, 2021: 120, 2020: 60,
                  2019: 40, 2018: 30, 2017: 20, 2016: 15, 2015: 10}
        stamps = []
        for year, n in counts.items():
            for k in range(n):
                stamps.append(ts(year, 1 + k % 11, 1 + k % 28))
        stamps.sort(reverse=True)
        rows = [(i // 5 * 160.0, stamps[i]) for i in range(0, len(stamps), 5)]
        return rows, rows[-1][0] + 160.0

    def test_old_years_keep_room_for_their_labels(self):
        # Regression: years before 2021 were squeezed into a few pixels at the bottom and lost their label.
        rows, height = self._old_years_library()
        span = 730
        rail = build_rail(rows, height - 800, span)
        years = [stop.caption for stop in rail.majors]
        self.assertEqual(years, [str(y) for y in range(2026, 2014, -1)])
        ys = [stop.pos * span for stop in rail.majors]
        for above, below in zip(ys, ys[1:]):
            self.assertGreaterEqual(below - above, LABEL_GAP - 0.01)
        self.assertLessEqual(ys[-1], span)

    def test_positions_follow_stop_order_and_stay_on_the_rail(self):
        rows, height = self._old_years_library()
        rail = build_rail(rows, height - 800, 730)
        poss = [stop.pos for stop in rail.stops]
        self.assertEqual(poss, sorted(poss))
        self.assertTrue(all(0.0 <= p <= 1.0 for p in poss))

    def test_scroll_and_rail_maps_are_inverse(self):
        rows, height = self._old_years_library()
        rail = build_rail(rows, height - 800, 730)
        previous = -1.0
        for step in range(0, 101):
            frac = step / 100
            pos = rail.pos_of(frac)
            self.assertGreaterEqual(pos, previous)
            previous = pos
            self.assertAlmostEqual(rail.frac_of(pos), frac, places=6)
        self.assertEqual(rail.pos_of(0.0), 0.0)
        self.assertEqual(rail.pos_of(1.0), 1.0)

    def test_year_start_marker_lines_up_with_its_dot(self):
        # The marker is drawn at pos_of(scroll fraction); at a year's first row it must sit on that year's boundary.
        rows, height = self._old_years_library()
        rail = build_rail(rows, height - 800, 730)
        for stop in rail.majors:
            if stop.frac < 1.0:
                self.assertAlmostEqual(rail.pos_of(stop.frac), stop.pos, places=6)

    def test_nearest_pos(self):
        days = [(2026, m, 5) for m in range(1, 7)]
        rows, height = rows_for(days)
        rail = build_rail(rows, height - 100, span=600)
        target = rail.stops[2]
        self.assertEqual(rail.nearest_pos(target.pos + 0.001, 0.01).row, target.row)
        self.assertIsNone(rail.nearest_pos(target.pos + 0.05, 0.001))

    def test_single_year_is_identity_like(self):
        rows, height = rows_for([(2026, m, 5) for m in range(1, 7)])
        rail = build_rail(rows, height - 100, span=600)
        self.assertEqual(rail.stops[0].pos, 0.0)
        self.assertAlmostEqual(rail.pos_of(0.5), 0.5, places=6)

    def test_empty(self):
        self.assertEqual(build_rail([], 100, 100).stops, ())


if __name__ == "__main__":
    unittest.main()
