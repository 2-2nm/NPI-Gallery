import importlib.util
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _load_geometry():
    """geometry.py has no Qt imports; load it directly so these tests run without PySide6."""
    path = Path(__file__).resolve().parent.parent / "npigallery" / "gui" / "viewer" / "geometry.py"
    spec = importlib.util.spec_from_file_location("viewer_geometry", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


geo = _load_geometry()


class FilmstripMathTests(unittest.TestCase):
    def test_centres_the_current_photo_in_a_long_strip(self):
        width, count, row = 600, 4407, 8
        scroll = geo.strip_scroll(row, count, width)
        centre = geo.STRIP_PAD + row * geo.STRIP_SLOT + geo.STRIP_SLOT / 2 - scroll
        self.assertAlmostEqual(centre, width / 2)

    def test_clamped_at_both_ends(self):
        width, count = 600, 100
        self.assertEqual(geo.strip_scroll(0, count, width), 0.0)
        self.assertEqual(geo.strip_scroll(count - 1, count, width), geo.strip_total(count) - width)

    def test_short_strip_is_centred_as_a_whole(self):
        width, count = 600, 3
        scroll = geo.strip_scroll(1, count, width)
        self.assertLess(scroll, 0)
        self.assertAlmostEqual(-scroll * 2 + geo.strip_total(count), width)

    def test_visible_range_and_hit_testing_agree(self):
        width, count = 600, 4407
        scroll = geo.strip_scroll(500, count, width)
        visible = geo.strip_visible(scroll, width, count)
        self.assertTrue(0 < len(visible) < 20)
        index = visible[len(visible) // 2]
        x = geo.STRIP_PAD + index * geo.STRIP_SLOT - scroll + geo.STRIP_THUMB_W / 2
        self.assertEqual(geo.strip_slot_at(x, scroll, count), index)

    def test_click_outside_the_items_is_ignored(self):
        self.assertIsNone(geo.strip_slot_at(-5, 0.0, 10))
        self.assertIsNone(geo.strip_slot_at(10_000, 0.0, 10))

    def test_empty_strip(self):
        self.assertEqual(len(geo.strip_visible(0.0, 500, 0)), 0)


class TopRowPlanTests(unittest.TestCase):
    LEFT = ["info", "copy", "print", "wallpaper", "slideshow", "delete", "more"]
    RIGHT = ["rotate", "favorite"]
    MARGIN, GAP = 16, 8

    def plan(self, width, left=None, right=None, has_edit=True):
        return geo.plan_top(
            width, left or self.LEFT, right or self.RIGHT, geo.COLLAPSE_ORDER, has_edit, self.MARGIN, self.GAP
        )

    def test_everything_fits_when_wide(self):
        self.assertEqual(self.plan(2000), set())

    def test_collapses_in_priority_order(self):
        wide = 2000
        for width in range(wide, 300, -5):
            hidden = self.plan(width)
            order = [k for k in geo.COLLAPSE_ORDER if k in hidden]
            self.assertEqual(order, geo.COLLAPSE_ORDER[: len(order)], width)

    def test_never_hides_info_delete_more_or_edit(self):
        hidden = self.plan(0)
        self.assertTrue({"info", "delete", "more"}.isdisjoint(hidden))

    def test_title_keeps_its_room_whenever_possible(self):
        for width in range(500, 1400, 11):
            hidden = self.plan(width)
            left = [k for k in self.LEFT if k not in hidden]
            right = [k for k in self.RIGHT if k not in hidden]
            room = geo.title_room(width, len(left), len(right), True, self.MARGIN, self.GAP)
            if len(hidden) < len(geo.COLLAPSE_ORDER):
                self.assertGreaterEqual(room, geo.TITLE_MIN, width)

    def test_pill_widths(self):
        self.assertEqual(geo.right_width(0, False), 0)
        self.assertEqual(geo.right_width(0, True), 2 * geo.TOOLBAR_PAD + geo.TOOLBAR_EDIT_WIDTH)
        self.assertGreater(geo.right_width(2, True), geo.right_width(1, True))
        self.assertGreater(geo.left_width(6), geo.left_width(3))

    def test_title_is_centred_on_the_window_when_it_fits(self):
        x, span = geo.title_span(1200, 300, 900, 260, self.GAP)
        self.assertEqual(span, 260)
        self.assertEqual(x + span / 2, 600)

    def test_title_squeezes_into_the_free_space_when_off_centre(self):
        x, span = geo.title_span(900, 100, 700, 500, self.GAP)  # right group much wider than the left
        self.assertLessEqual(x + span, 700 - self.GAP)
        self.assertGreaterEqual(x, 100 + self.GAP)

    def test_title_is_centred_even_with_unequal_groups(self):
        x, span = geo.title_span(1200, 200, 1000, 300, self.GAP)
        self.assertEqual(x + span / 2, 600)


class SafeInsetsTests(unittest.TestCase):
    SIDE = 16 + geo.NAV_WIDTH + 8

    def test_rows_and_nav_buttons_are_cleared(self):
        left, top, right, bottom = geo.safe_insets(16, 8, 46, 58, 0.0, 300)
        self.assertEqual((left, right), (self.SIDE, self.SIDE))
        self.assertEqual(top, 16 + 46 + 8)
        self.assertEqual(bottom, 16 + 58 + 8)

    def test_info_card_inset_follows_the_animation(self):
        full = geo.safe_insets(16, 8, 46, 58, 1.0, 300)[0]
        self.assertEqual(full, self.SIDE + 300 + 8)
        half = geo.safe_insets(16, 8, 46, 58, 0.5, 300)[0]
        self.assertEqual(half, self.SIDE + round((300 + 8) / 2))
        self.assertEqual(geo.safe_insets(16, 8, 46, 58, 7.0, 300)[0], full)  # clamped


class TextTests(unittest.TestCase):
    def test_time_parts_use_a_twelve_hour_clock(self):
        from datetime import datetime

        stamp = datetime(2026, 11, 12, 10, 3).timestamp()
        self.assertEqual(geo.time_parts(stamp), ("November", "12", "2026", "10", "03", "AM"))
        self.assertEqual(geo.long_date(stamp), "November 12, 2026 · 10:03 AM")
        afternoon = datetime(2026, 1, 5, 0, 7).timestamp()
        self.assertEqual(geo.time_parts(afternoon)[3:], ("12", "07", "AM"))
        noon = datetime(2026, 1, 5, 12, 0).timestamp()
        self.assertEqual(geo.time_parts(noon)[3:], ("12", "00", "PM"))

    def test_subtitle_adds_the_pixel_size_after_the_date(self):
        from datetime import datetime

        stamp = datetime(2026, 11, 12, 10, 3).timestamp()
        self.assertEqual(geo.subtitle(stamp, 1000, 1000), "November 12, 2026 · 10:03 AM · 1000x1000")
        self.assertEqual(geo.subtitle(stamp, 0, 0), "November 12, 2026 · 10:03 AM")
        self.assertEqual(geo.subtitle(1e30, 640, 480), "640x480")

    def test_bad_timestamp(self):
        self.assertIsNone(geo.time_parts(1e30))
        self.assertEqual(geo.long_date(1e30), "")

    def test_paths_get_break_opportunities_without_changing_visible_text(self):
        path = "C:\\Users\\Zephyrus\\Pictures\\Screenshot (1693).png"
        wrapped = geo.breakable(path)
        self.assertIn("\\\u200b", wrapped)
        self.assertEqual(wrapped.replace("\u200b", ""), path)

    def test_bit_depth(self):
        self.assertEqual(geo.bit_depth("RGBA"), "32 bit")
        self.assertEqual(geo.bit_depth("RGB"), "24 bit")
        self.assertEqual(geo.bit_depth("???"), "-")


try:
    from PIL import Image
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from npigallery.gui.viewer import ViewerHooks, ViewerWindow
    from npigallery.gui.viewer.canvas import ZOOM_FACTOR
    from npigallery.services.scanner import scan_folder

    HAS_PYSIDE = True
except Exception:
    HAS_PYSIDE = False


@unittest.skipUnless(HAS_PYSIDE, "PySide6 not installed")
class ViewerWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for index in range(4):
            Image.new("RGB", (160 + index * 10, 100), (30 * index, 90, 160)).save(root / f"p{index}.png")
        self.items = scan_folder(root)
        self.favorites: set[str] = set()
        self.deleted: list[str] = []
        hooks = ViewerHooks(
            is_favorite=lambda item: str(item.path) in self.favorites,
            toggle_favorite=lambda item: self.favorites.symmetric_difference_update({str(item.path)}),
            delete=self._delete,
            rotate=self._rotate_file,
        )
        self.rotations: list[int] = []
        self.rotate_fails = False
        self.viewer = ViewerWindow(self.items, 0, None, hooks=hooks)
        self.viewer.show()

    def tearDown(self):
        self.viewer.close()
        self._tmp.cleanup()

    def _delete(self, item, parent):
        self.deleted.append(item.name)
        return True

    def _rotate_file(self, item, degrees, parent):
        self.rotations.append(degrees)
        if self.rotate_fails:
            return None
        from npigallery.services.rotate import rotate_file
        from npigallery.services.scanner import describe_media

        rotate_file(item.path, degrees)
        return describe_media(item.path)

    def _wait_loaded(self):
        for _ in range(100):
            QApplication.processEvents()
            if self.viewer._current is not None:
                return
            QTest.qWait(20)
        self.fail("photo never loaded")

    def test_is_a_top_level_window_with_min_max_buttons(self):
        self.assertTrue(self.viewer.isWindow())
        self.assertIsNone(self.viewer.parent())
        flags = self.viewer.windowFlags()
        self.assertTrue(flags & Qt.WindowType.WindowMinMaxButtonsHint)

    def test_loads_and_navigates(self):
        self._wait_loaded()
        self.assertEqual(self.viewer._counter._text, f"1 of {len(self.items)}")
        QTest.keyClick(self.viewer, Qt.Key.Key_Right)
        self.assertEqual(self.viewer._row, 1)
        QTest.keyClick(self.viewer, Qt.Key.Key_End)
        self.assertEqual(self.viewer._row, len(self.items) - 1)
        QTest.keyClick(self.viewer, Qt.Key.Key_Right)  # no wrap past the end
        self.assertEqual(self.viewer._row, len(self.items) - 1)

    def test_info_card_toggles(self):
        self._wait_loaded()
        QTest.keyClick(self.viewer, Qt.Key.Key_I)
        self.assertTrue(self.viewer._info_open)
        QTest.keyClick(self.viewer, Qt.Key.Key_I)
        self.assertFalse(self.viewer._info_open)

    def test_favorite_and_delete_go_through_the_hooks(self):
        self._wait_loaded()
        self.viewer._toggle_favorite()
        self.assertEqual(len(self.favorites), 1)
        self.assertTrue(self.viewer._actions.button("favorite").isChecked())
        self.viewer._delete()
        self.assertEqual(self.deleted, ["p0.png"])
        self.assertEqual(len(self.viewer._items), len(self.items) - 1)

    def test_narrow_window_moves_buttons_into_the_menu(self):
        self.viewer.resize(*(1000, 500))
        QApplication.processEvents()
        wide = self.viewer._tools.width() + self.viewer._actions.width()
        self.viewer.resize(*(640, 500))
        QApplication.processEvents()
        self.assertLessEqual(self.viewer._tools.width() + self.viewer._actions.width(), wide)
        controls = self.viewer._controls
        self.assertLessEqual(controls.x() + controls.width(), self.viewer.width())

    def test_top_row_layout(self):
        self.viewer.resize(*(1200, 700))
        QApplication.processEvents()
        v = self.viewer
        self.assertEqual(v._tools.x(), 16)  # Info card button leads the left pill
        self.assertLess(v._tools.x(), v._title.x())
        self.assertLess(v._title.x() + v._title.width(), v._actions.x())
        self.assertLess(v._actions.x() + v._actions.width(), v._controls.x())
        self.assertEqual(v._controls.x() + v._controls.width(), v.width() - 16)
        centre = v._title.x() + v._title.width() / 2
        self.assertAlmostEqual(centre, v.width() / 2, delta=1)
        self.assertLess(v._tools.button("info").x(), v._tools.button("delete").x())
        self.assertLess(v._actions.button("rotate").x(), v._actions.button("favorite").x())
        self.assertLess(v._actions.button("favorite").x(), v._actions._edit.x())

    def test_photo_rests_clear_of_the_top_and_bottom_rows(self):
        self._wait_loaded()
        self.viewer.resize(*(1000, 700))
        QApplication.processEvents()
        canvas = self.viewer._canvas
        canvas.fit(upscale=True)
        rect = canvas.mapFromScene(canvas._item.sceneBoundingRect()).boundingRect()
        _, top, _, bottom = canvas._insets
        self.assertGreaterEqual(rect.top(), top - 2)
        self.assertLessEqual(rect.bottom(), canvas.viewport().height() - bottom + 2)

    def test_info_card_pushes_the_fitted_photo_aside(self):
        self._wait_loaded()
        canvas = self.viewer._canvas
        canvas.fit(upscale=True)
        before = canvas.mapFromScene(canvas._item.sceneBoundingRect().center()).x()
        self.viewer._set_info(True)
        self.viewer._info_animation.stop()
        self.viewer._on_info_progress(1.0)
        QApplication.processEvents()
        after = canvas.mapFromScene(canvas._item.sceneBoundingRect().center()).x()
        self.assertLess(after, before)

    def _canvas_with_photo(self):
        self._wait_loaded()
        self.viewer.resize(*(1000, 700))
        QApplication.processEvents()
        return self.viewer._canvas

    def test_cannot_zoom_out_below_the_resting_size(self):
        canvas = self._canvas_with_photo()
        canvas.fit(upscale=True)
        resting = canvas.transform().m11()
        canvas.zoom_by(1 / ZOOM_FACTOR)
        self.assertAlmostEqual(canvas.transform().m11(), resting)
        canvas.zoom_by(ZOOM_FACTOR)
        canvas.zoom_by(ZOOM_FACTOR)
        canvas.zoom_by(1 / ZOOM_FACTOR)
        canvas.zoom_by(1 / ZOOM_FACTOR)
        canvas.zoom_by(1 / ZOOM_FACTOR)  # overshoots: lands back on the resting size
        self.assertAlmostEqual(canvas.transform().m11(), resting)
        self.assertTrue(canvas.is_fit())

    def test_smooth_zoom_glides_to_the_goal_and_lands_on_the_resting_size(self):
        import time

        canvas = self._canvas_with_photo()
        canvas.fit(upscale=True)
        resting = canvas.transform().m11()
        seen = []
        canvas.zoom_changed.connect(seen.append)

        def settle(seconds=0.5):
            end = time.time() + seconds
            while time.time() < end:
                QApplication.processEvents()

        canvas.zoom_smooth(ZOOM_FACTOR)
        canvas.zoom_smooth(ZOOM_FACTOR)
        settle()
        self.assertGreater(len(seen), 2)  # it moved in steps, not in one jump
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(canvas.transform().m11(), resting * ZOOM_FACTOR**2, places=4)
        for _ in range(4):
            canvas.zoom_smooth(1 / ZOOM_FACTOR)  # overshoots: lands back on the resting size
        settle()
        self.assertAlmostEqual(canvas.transform().m11(), resting)
        self.assertTrue(canvas.is_fit())

    def test_wheel_zoom_hides_the_controls_only_when_the_setting_is_on(self):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent

        from npigallery.gui.theme.preferences import prefs

        canvas = self._canvas_with_photo()
        fader = self.viewer._fader
        fader.snap(1.0)

        def wheel():
            pos = QPointF(400, 300)
            canvas.wheelEvent(
                QWheelEvent(pos, canvas.viewport().mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            )

        prefs().set("hide_controls_on_zoom", False)
        wheel()
        self.assertTrue(fader.is_shown())
        prefs().set("hide_controls_on_zoom", True)
        try:
            wheel()
            self.assertFalse(fader.is_shown())
            self.assertFalse(self.viewer._idle.isActive())
        finally:
            prefs().set("hide_controls_on_zoom", False)

    def test_click_in_the_edge_zones_steps_photos(self):
        canvas = self._canvas_with_photo()
        steps = []
        canvas.step_requested.connect(steps.append)
        view = canvas.viewport()
        width, y = view.width(), view.height() // 2
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(int(width * 0.95), y))
        self.assertEqual(steps, [1])  # photo 0 has a next one
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(int(width * 0.05), y))
        self.assertEqual(steps, [1])  # ...but no previous one
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(width // 2, y))
        self.assertEqual(steps, [1])

    def test_rotate_rewrites_the_file_once_the_clicking_settles(self):
        self._wait_loaded()
        path = self.viewer._items[0].path
        QTest.keyClick(self.viewer, Qt.Key.Key_R)
        QTest.keyClick(self.viewer, Qt.Key.Key_R)
        QTest.keyClick(self.viewer, Qt.Key.Key_R)
        self.assertEqual(self.rotations, [])  # still waiting: one rewrite for all the clicks
        self.viewer._flush_rotation()
        self.assertEqual(self.rotations, [270])
        self.assertEqual(Image.open(path).size, (100, 160))
        self.assertEqual((self.viewer._items[0].width, self.viewer._items[0].height), (100, 160))

    def test_a_full_turn_writes_nothing(self):
        self._wait_loaded()
        for _ in range(4):
            QTest.keyClick(self.viewer, Qt.Key.Key_R)
        self.viewer._flush_rotation()
        self.assertEqual(self.rotations, [])

    def test_leaving_the_photo_writes_the_pending_turn(self):
        self._wait_loaded()
        QTest.keyClick(self.viewer, Qt.Key.Key_R)
        QTest.keyClick(self.viewer, Qt.Key.Key_Right)
        self.assertEqual(self.rotations, [90])

    def test_a_failed_rotation_puts_the_view_back(self):
        self._wait_loaded()
        self.rotate_fails = True
        QTest.keyClick(self.viewer, Qt.Key.Key_R)
        self.assertEqual(self.viewer._canvas._angle, 90)
        self.viewer._flush_rotation()
        self.assertEqual(self.rotations, [90])
        self.assertEqual(self.viewer._canvas._angle, 0)

    def test_window_buttons(self):
        self.viewer._toggle_maximized()
        QApplication.processEvents()
        self.assertTrue(self.viewer.isMaximized() or self.viewer.isFullScreen())
        self.viewer._toggle_maximized()
        QApplication.processEvents()
        self.assertFalse(self.viewer.isMaximized())

    def test_chrome_fades_out_and_returns_on_mouse_move(self):
        self._wait_loaded()
        self.viewer._fader.snap(0.0)
        self.assertFalse(self.viewer._fader.is_shown())
        self.viewer._poke()
        self.assertTrue(self.viewer._fader.is_shown())

    def test_photo_shows_a_normal_arrow_cursor(self):
        self.assertEqual(self.viewer._canvas.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)

    def _open_info_name(self):
        self._wait_loaded()
        self.viewer._set_info(True)
        QApplication.processEvents()
        return self.viewer._info._name

    def test_info_name_is_renamed_in_place_through_the_hook(self):
        edit = self._open_info_name()
        calls = []

        def rename(item, stem, parent):
            calls.append(stem)
            name = stem + item.path.suffix
            return replace(item, path=item.path.with_name(name), name=name)

        self.viewer._hooks.rename = rename
        self.viewer._info.set_renamable(True)
        self.assertEqual(edit.text(), "p0")
        edit.begin_edit(select_all=False)
        edit.setText("holiday")
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.assertEqual(calls, ["holiday"])
        self.assertEqual(self.viewer._items[0].name, "holiday.png")
        self.assertEqual(edit.text(), "holiday")
        self.assertFalse(edit.is_editing())

    def test_typing_in_the_name_never_navigates_photos(self):
        edit = self._open_info_name()
        self.viewer._hooks.rename = lambda item, stem, parent: None
        self.viewer._info.set_renamable(True)
        edit.begin_edit(select_all=False)
        edit.setText("abc")
        QTest.keyClick(self.viewer, Qt.Key.Key_Backspace)  # even if the window got it
        self.assertEqual(edit.text(), "ab")
        self.assertEqual(self.viewer._row, 0)

    def test_escape_cancels_a_rename_without_closing_the_viewer(self):
        edit = self._open_info_name()
        called = []
        self.viewer._hooks.rename = lambda item, stem, parent: called.append(stem)
        self.viewer._info.set_renamable(True)
        edit.begin_edit(select_all=False)
        edit.setText("nope")
        QTest.keyClick(edit, Qt.Key.Key_Escape)
        self.assertEqual(called, [])
        self.assertEqual(edit.text(), "p0")
        self.assertTrue(self.viewer.isVisible())

    def test_fullscreen_round_trip_keeps_window_usable(self):
        self.viewer._toggle_fullscreen()
        self.assertTrue(self.viewer.isFullScreen())
        self.viewer._toggle_fullscreen()
        self.assertFalse(self.viewer.isFullScreen())


if __name__ == "__main__":
    unittest.main()


class VideoMathTests(unittest.TestCase):
    def test_seek_zones_are_the_outer_fifth(self):
        self.assertEqual(geo.seek_zone(0, 1000), -1)
        self.assertEqual(geo.seek_zone(200, 1000), -1)
        self.assertEqual(geo.seek_zone(201, 1000), 0)
        self.assertEqual(geo.seek_zone(500, 1000), 0)
        self.assertEqual(geo.seek_zone(799, 1000), 0)
        self.assertEqual(geo.seek_zone(800, 1000), 1)
        self.assertEqual(geo.seek_zone(10, 0), 0)

    def test_clock_text(self):
        self.assertEqual(geo.clock(0), "0:00")
        self.assertEqual(geo.clock(187_000), "3:07")
        self.assertEqual(geo.clock(3_729_000), "1:02:09")
        self.assertEqual(geo.clock(-5), "0:00")

    def test_seek_position_is_clamped(self):
        self.assertEqual(geo.position_at(8, 8, 100, 60_000), 0)
        self.assertEqual(geo.position_at(58, 8, 100, 60_000), 30_000)
        self.assertEqual(geo.position_at(500, 8, 100, 60_000), 60_000)
        self.assertEqual(geo.position_at(50, 8, 100, 0), 0)
        self.assertEqual(geo.clamp_position(70_000, 60_000), 60_000)
        self.assertEqual(geo.clamp_position(-1, 60_000), 0)
