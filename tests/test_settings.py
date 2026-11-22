import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QDialog

    from npigallery.config import APP_NAME
    from npigallery.gui.theme import theme
    from npigallery.gui.common.motion import DROPDOWN_DURATION
    from npigallery.gui.theme.preferences import DEFAULTS, prefs
    from npigallery.gui.settings.page import SettingsPage

    HAS_PYSIDE = True
except Exception:
    HAS_PYSIDE = False


@unittest.skipUnless(HAS_PYSIDE, "PySide6 not installed")
class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        theme.apply_theme(cls._app, "dark")

    def setUp(self):
        QSettings(APP_NAME, APP_NAME).clear()

    def test_defaults(self):
        self.assertEqual(prefs().get("theme"), DEFAULTS["theme"])
        self.assertTrue(prefs().get("confirm_delete"))
        self.assertFalse(prefs().get("location_features"))

    def test_clear_card_shows_how_much_the_cache_holds(self):
        page = SettingsPage()
        description = page.clear_card.description_label
        page.set_cache_usage(4210, 128 * 1024 * 1024)
        self.assertIn("128.0 MB", description.text())
        self.assertIn("4,210 thumbnails", description.text())
        page.set_cache_usage(1, 2048)
        self.assertIn("(1 thumbnail)", description.text())
        page.set_cache_usage(0, 0)
        self.assertIn("No thumbnails", description.text())
        self.assertIn("generated again", description.text())  # the explanation stays

    def test_dropdown_animation_matches_info_card_pace(self):
        self.assertLessEqual(DROPDOWN_DURATION, 300)
        self.assertGreaterEqual(DROPDOWN_DURATION, 150)

    def test_theme_switch(self):
        theme.apply_theme(self._app, "light")
        self.assertEqual(theme.palette().name, "light")
        theme.apply_theme(self._app, "dark")
        self.assertEqual(theme.palette().name, "dark")

    def test_page_controls_write_preferences(self):
        page = SettingsPage()
        page.tile_switch.switch.setChecked(False)
        self.assertFalse(prefs().get("tile_attributes"))
        page.wheel_dropdown._choose("navigate")
        self.assertEqual(prefs().get("mouse_wheel"), "navigate")
        page.zoom_dropdown._choose("fit")
        self.assertEqual(prefs().get("zoom_preference"), "fit")
        page.theme_dropdown._choose("light")
        self.assertEqual(prefs().get("theme"), "light")
        self.assertTrue(prefs().get("auto_hide_controls"))
        page.autohide_switch.switch.setChecked(False)
        self.assertFalse(prefs().get("auto_hide_controls"))
        page.autohide_switch.switch.setChecked(True)
        self.assertFalse(prefs().get("hide_controls_on_zoom"))
        page.zoom_hide_switch.switch.setChecked(True)
        self.assertTrue(prefs().get("hide_controls_on_zoom"))
        page.zoom_hide_switch.switch.setChecked(False)

    def test_categories_are_listed_in_order(self):
        page = SettingsPage()
        self.assertEqual(
            page.category_keys(),
            ["personalization", "general", "photos", "video", "files", "storage", "about"],
        )
        self.assertEqual(page.current_category(), "personalization")
        self.assertTrue(page._nav_items["personalization"].isChecked())

    def test_nav_switches_category(self):
        page = SettingsPage()
        for key in page.category_keys():
            page._nav_items[key].click()
            self.assertEqual(page.current_category(), key)
            self.assertTrue(page._nav_items[key].isChecked())
            self.assertEqual(sum(item.isChecked() for item in page._nav_items.values()), 1)

    def test_nav_highlight_follows_selection(self):
        page = SettingsPage()
        page.show_category("storage")
        self.assertEqual(page._nav_list.current, "storage")
        self.assertTrue(page._nav_items["storage"].isChecked())
        self.assertEqual(page.current_category(), "storage")
        page.show_category("general")
        self.assertEqual(page._nav_list.current, "general")

    def test_nav_is_centred_on_the_sidebar_not_on_the_page(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QWidget

        host = QWidget()
        host.resize(1200, 800)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(240)
        row.addWidget(sidebar)
        column = QVBoxLayout()
        column.setSpacing(0)
        top_bar = QWidget()
        top_bar.setFixedHeight(66)  # the page starts below the top bar, so its own middle is 33 px lower
        column.addWidget(top_bar)
        page = SettingsPage()
        column.addWidget(page, 1)
        row.addLayout(column, 1)
        page.set_center_reference(sidebar)
        host.show()
        QApplication.processEvents()
        nav = page._nav_list
        nav_middle = nav.mapTo(host, QPoint(0, nav.height() // 2)).y()
        sidebar_middle = sidebar.mapTo(host, QPoint(0, sidebar.height() // 2)).y()
        self.assertLessEqual(abs(nav_middle - sidebar_middle), 2)

    def test_controls_live_on_their_category_page(self):
        page = SettingsPage()
        expected = {
            "personalization": [page.theme_dropdown, page.accent_card, page.background_card],
            "general": [page.location_switch, page.confirm_switch],
            "photos": [page.tile_switch, page.wheel_dropdown, page.zoom_dropdown, page.autohide_switch, page.zoom_hide_switch],
            "files": [page.dump_button, page.restore_button],
            "storage": [page.clear_button],
            "about": [page.about],
        }
        for key, widgets in expected.items():
            for widget in widgets:
                self.assertTrue(page._pages[key].isAncestorOf(widget), f"{key}: {widget}")

    def _picture(self, name: str) -> str:
        path = os.path.join(tempfile.mkdtemp(), name)
        image = QImage(40, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#336699"))
        image.save(path)
        return path

    def _background_card(self, old_path: str):
        from npigallery.gui.settings.page import SidebarBackgroundCard

        prefs().set("sidebar_bg_mode", "picture")
        prefs().set("sidebar_bg_path", old_path)
        prefs().set("sidebar_bg_crop", "0.2000,0.3000,2.000")
        return SidebarBackgroundCard()

    def _fake_dialog(self, result):
        class Dialog:
            DialogCode = QDialog.DialogCode

            def __init__(self, *args):
                self.args = args

            def exec(self):
                return result

            def crop(self):
                return (0.7, 0.8, 3.0)

        return Dialog

    def test_new_sidebar_picture_is_only_applied_after_done(self):
        old, new = self._picture("old.png"), self._picture("new.png")
        card = self._background_card(old)
        changes = []
        prefs().changed.connect(changes.append)

        # picking the file alone must not touch the sidebar
        with mock.patch("npigallery.gui.settings.page.QFileDialog.getOpenFileName", return_value=(new, "")):
            self.assertEqual(card.choose(), new)
        self.assertEqual(prefs().get("sidebar_bg_path"), old)
        self.assertEqual(changes, [])

        # Cancel in the crop dialog: still nothing changed
        with mock.patch("npigallery.gui.settings.page.SidebarCropDialog", self._fake_dialog(QDialog.DialogCode.Rejected)):
            self.assertFalse(card.crop(new))
        self.assertEqual(prefs().get("sidebar_bg_path"), old)
        self.assertEqual(prefs().get("sidebar_bg_crop"), "0.2000,0.3000,2.000")
        self.assertEqual(changes, [])

        # Done: picture and framing are applied together
        with mock.patch("npigallery.gui.settings.page.SidebarCropDialog", self._fake_dialog(QDialog.DialogCode.Accepted)):
            self.assertTrue(card.crop(new))
        self.assertEqual(prefs().get("sidebar_bg_path"), new)
        self.assertEqual(prefs().get("sidebar_bg_crop"), "0.7000,0.8000,3.000")

    def test_cancelling_first_picture_keeps_background_off(self):
        new = self._picture("first.png")
        from npigallery.gui.settings.page import SidebarBackgroundCard

        card = SidebarBackgroundCard()
        with mock.patch("npigallery.gui.settings.page.QFileDialog.getOpenFileName", return_value=(new, "")), \
                mock.patch("npigallery.gui.settings.page.SidebarCropDialog", self._fake_dialog(QDialog.DialogCode.Rejected)):
            card._on_mode("picture")
        self.assertEqual(prefs().get("sidebar_bg_mode"), "none")
        self.assertEqual(prefs().get("sidebar_bg_path"), "")

    def test_settings_wheel_scroll_glides_and_stacks(self):
        import time

        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication

        page = SettingsPage()
        page.resize(900, 300)
        page.show()
        QApplication.processEvents()
        scroll = page._scroll
        bar = scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 400)
        seen = []
        bar.valueChanged.connect(seen.append)

        def wheel(delta):
            pos = QPointF(300, 150)
            scroll.wheelEvent(
                QWheelEvent(pos, scroll.viewport().mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            )

        def settle(seconds=0.4):
            end = time.time() + seconds
            while time.time() < end:
                QApplication.processEvents()

        wheel(-120)
        settle()
        self.assertGreater(len(seen), 2)  # it glided instead of jumping
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(bar.value(), 130)
        wheel(-120)
        wheel(-120)  # a quick second notch stacks onto the first
        settle()
        self.assertEqual(bar.value(), 390)
        wheel(120 * 50)  # far past the top: clamps
        settle()
        self.assertEqual(bar.value(), 0)

    def test_dump_and_restore_buttons_emit_signals(self):
        page = SettingsPage()
        hits = []
        page.dump_requested.connect(lambda: hits.append("dump"))
        page.restore_requested.connect(lambda: hits.append("restore"))
        page.dump_button.click()
        page.restore_button.click()
        self.assertEqual(hits, ["dump", "restore"])

    def test_clear_cache_signal(self):
        page = SettingsPage()
        hits = []
        page.clear_cache_requested.connect(lambda: hits.append(1))
        page.clear_button.click()
        self.assertEqual(hits, [1])


    def _page_in_window(self, height=900):
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

        host = QWidget()
        host.resize(1400, height)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(260)
        row.addWidget(sidebar)
        column = QVBoxLayout()
        column.setSpacing(0)
        top_bar = QWidget()
        top_bar.setFixedHeight(66)
        column.addWidget(top_bar)
        page = SettingsPage()
        column.addWidget(page, 1)
        row.addLayout(column, 1)
        page.set_center_reference(sidebar)
        host.show()
        self._settle()
        return host, sidebar, page

    def _settle(self, seconds=0.0):
        import time

        end = time.time() + seconds
        QApplication.processEvents()
        while time.time() < end:
            QApplication.processEvents()
        QApplication.processEvents()

    def test_every_category_is_one_section_of_the_same_column(self):
        host, _sidebar, page = self._page_in_window()
        sections = [page._pages[key] for key in page.category_keys()]
        for section in sections:
            self.assertTrue(page._scroll.widget().isAncestorOf(section))
        self.assertEqual([s.y() for s in sections], sorted(s.y() for s in sections))  # same order as the list

    def test_clicking_a_category_glides_its_section_to_the_sidebar_middle(self):
        from PySide6.QtCore import QPoint

        host, sidebar, page = self._page_in_window()
        page._nav_items["photos"].click()
        self._settle(0.9)
        section = page._pages["photos"]
        section_middle = section.mapTo(host, QPoint(0, section.height() // 2)).y()
        sidebar_middle = sidebar.mapTo(host, QPoint(0, sidebar.height() // 2)).y()
        self.assertLessEqual(abs(section_middle - sidebar_middle), 3)
        self.assertEqual(page.current_category(), "photos")

    def test_scrolling_selects_the_category_on_the_middle_line(self):
        host, _sidebar, page = self._page_in_window()
        bar = page._scroll.verticalScrollBar()
        page._scroll.user_scrolled.emit()
        for key in ("storage", "general", "files"):
            bar.setValue(page._section_target(key))
            self._settle()
            self.assertEqual(page.current_category(), key)
            self.assertTrue(page._nav_items[key].isChecked())

    def test_end_of_the_column_scrolls_out_of_the_bottom_dimming(self):
        from PySide6.QtCore import QPoint

        host, _sidebar, page = self._page_in_window()
        scroll = page._scroll
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        self._settle()
        last = page._pages["about"]
        bottom = last.mapTo(scroll, QPoint(0, last.height())).y()
        self.assertLessEqual(bottom, scroll.height() - scroll.fade_height() + 2)

    def test_list_keeps_its_choice_while_a_click_is_still_gliding(self):
        host, _sidebar, page = self._page_in_window()
        page._nav_items["files"].click()
        self._settle(0.1)  # the column passes other sections on its way
        self.assertEqual(page.current_category(), "files")
        self.assertTrue(page._nav_items["files"].isChecked())


if __name__ == "__main__":
    unittest.main()