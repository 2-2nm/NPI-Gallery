import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from npigallery.config import APP_NAME
    from npigallery.gui.theme import accent, theme
    from npigallery.gui.settings.accent_picker import PAIR_GAP, AccentDialog, CustomColorDialog
    from npigallery.gui.theme.preferences import prefs
    from npigallery.gui.settings.page import SettingsPage

    HAS_PYSIDE = True
except Exception:
    HAS_PYSIDE = False


@unittest.skipUnless(HAS_PYSIDE, "PySide6 not installed")
class AccentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        QSettings(APP_NAME, APP_NAME).clear()
        theme.apply_theme(self._app, "dark")

    def tearDown(self):
        QSettings(APP_NAME, APP_NAME).clear()
        theme.apply_theme(self._app, "dark")

    def test_normalize(self):
        self.assertEqual(accent.normalize("#ABC"), "#aabbcc")
        self.assertEqual(accent.normalize("0078D4"), "#0078d4")
        self.assertIsNone(accent.normalize("nope"))
        self.assertIsNone(accent.normalize(""))

    def test_on_color_picks_readable_text(self):
        self.assertEqual(accent.on_color("#ffffff"), accent.DARK_TEXT)
        self.assertEqual(accent.on_color("#000000"), accent.LIGHT_TEXT)
        self.assertEqual(accent.on_color("#c8c8c8"), accent.DARK_TEXT)

    def test_readable_on_adjusts_weak_contrast(self):
        light = theme.LIGHT
        adjusted = accent.readable_on("#c8c8c8", light.float_bg, False)
        self.assertGreaterEqual(accent.contrast_ratio(adjusted, light.float_bg), accent.MIN_CONTRAST)
        dark = theme.DARK
        adjusted = accent.readable_on("#202020", dark.float_bg, True)
        self.assertGreaterEqual(accent.contrast_ratio(adjusted, dark.float_bg), accent.MIN_CONTRAST)

    def test_readable_on_keeps_good_colors(self):
        self.assertEqual(accent.readable_on("#0078d4", theme.DARK.float_bg, True), "#0078d4")

    def test_default_mode_keeps_original_palette(self):
        self.assertIs(accent.apply(theme.DARK), theme.DARK)
        self.assertEqual(theme.palette().nav_active_bg, theme.DARK.nav_active_bg)

    def test_manual_mode_recolors_every_active_marker(self):
        accent.choose("#e3008c")
        palette = theme.palette()
        for role in ("accent", "nav_active_bg", "focus_line", "checked_border", "default_bg", "timeline_active"):
            self.assertEqual(getattr(palette, role), "#e3008c")
        self.assertEqual(palette.nav_active_fg, palette.on_accent)
        self.assertEqual(palette.name, "dark")

    def test_accent_survives_theme_switch(self):
        accent.choose("#10893e")
        theme.apply_theme(self._app, "light")
        self.assertEqual(theme.palette().name, "light")
        self.assertEqual(theme.palette().nav_active_bg, "#10893e")

    def test_recent_colors_are_unique_and_capped(self):
        for _, value in accent.PRESETS[:9]:
            accent.choose(value)
        accent.choose(accent.PRESETS[8][1])
        history = accent.recent()
        self.assertEqual(len(history), accent.RECENT_LIMIT)
        self.assertEqual(history[0], accent.PRESETS[8][1])
        self.assertEqual(len(set(history)), len(history))

    def test_invalid_color_is_ignored(self):
        accent.choose("garbage")
        self.assertEqual(accent.current_mode(), accent.MODE_SYSTEM)  # a fresh install follows Windows

    def test_old_default_mode_counts_as_windows_accent(self):
        prefs().set("accent_mode", "default")
        self.assertEqual(accent.current_mode(), accent.MODE_SYSTEM)

    def test_automatic_is_gray_without_a_sidebar_picture(self):
        prefs().set("accent_mode", accent.MODE_AUTOMATIC)
        self.assertEqual(accent.selection(), accent.GRAY_ACCENT)

    def test_dominant_color_follows_the_picture(self):
        from PIL import Image

        red = accent.to_hsv(accent.dominant_color(Image.new("RGB", (32, 32), (200, 30, 30))))
        self.assertTrue(red[0] < 0.05 or red[0] > 0.95)
        self.assertGreater(red[1], 0.4)
        self.assertEqual(accent.dominant_color(Image.new("RGB", (32, 32), (120, 120, 120))), accent.GRAY_ACCENT)
        self.assertEqual(accent.dominant_color(Image.new("RGB", (32, 32), (0, 0, 0))), accent.GRAY_ACCENT)

    def test_system_mode_falls_back_when_unavailable(self):
        prefs().set("accent_mode", accent.MODE_SYSTEM)
        self.assertIsNotNone(theme.palette())

    def test_settings_card_only_offers_view_colors(self):
        page = SettingsPage()
        card = page.accent_card
        self.assertTrue(card._details.isHidden())
        card.dropdown._choose(accent.MODE_MANUAL)
        self.assertEqual(prefs().get("accent_mode"), accent.MODE_MANUAL)
        self.assertFalse(card._details.isHidden())
        self.assertEqual(card.view_button.text(), "View colors")

    def test_dialog_stages_choice_until_done(self):
        accent.choose("#0078d4")
        dialog = AccentDialog()
        dialog.set_pending("#e3008c")
        self.assertEqual(prefs().get("accent_color"), "#0078d4")
        selected = [s.color() for s in dialog._swatches if s._selected]
        self.assertEqual(selected, ["#e3008c"])
        dialog.commit()
        self.assertEqual(prefs().get("accent_color"), "#e3008c")
        self.assertEqual(theme.palette().nav_active_bg, "#e3008c")

    def test_dialog_cancel_keeps_color(self):
        accent.choose("#0078d4")
        dialog = AccentDialog()
        dialog.set_pending("#e3008c")
        dialog.reject()
        self.assertEqual(prefs().get("accent_color"), "#0078d4")

    def test_adopted_custom_color_is_selected_and_saved(self):
        accent.choose("#0078d4")
        dialog = AccentDialog()
        dialog.adopt_custom("#123456")
        self.assertEqual(dialog.pending(), "#123456")
        self.assertEqual([s.color() for s in dialog._swatches if s._selected], ["#123456"])
        dialog.commit()
        self.assertEqual(accent.custom()[0], "#123456")
        self.assertEqual(accent.recent()[0], "#123456")
        self.assertEqual(theme.palette().nav_active_bg, "#123456")

    def test_adopted_color_skips_duplicate_swatch(self):
        accent.choose("#0078d4")
        dialog = AccentDialog()
        before = len(dialog._swatches)
        dialog.adopt_custom(accent.PRESETS[0][1])
        self.assertEqual(len(dialog._swatches), before)

    def test_custom_dialog_square_and_slider(self):
        dialog = CustomColorDialog("#0078d4")
        dialog._on_slider(1.0)
        dialog._on_square(0.0, 1.0)
        self.assertEqual(dialog.pending(), "#ff0000")
        dialog._on_slider(0.5)
        self.assertEqual(dialog.pending(), "#800000")
        dialog._on_square(0.0, 0.0)
        self.assertEqual(dialog.pending(), "#808080")

    def test_custom_dialog_hex_updates_all_fields(self):
        dialog = CustomColorDialog("#0078d4")
        dialog._on_hex("#336699")
        self.assertEqual(dialog.pending(), "#336699")
        self.assertEqual([f.value() for f in dialog._rgb], [51, 102, 153])
        self.assertEqual(dialog._hsv_fields[0].value(), 210)
        dialog._on_hex("#12")
        self.assertEqual(dialog.pending(), "#336699")

    def test_custom_dialog_rgb_fields_drive_color(self):
        dialog = CustomColorDialog("#0078d4")
        for field, number in zip(dialog._rgb, (255, 0, 0)):
            field.set_value(number)
        dialog._on_rgb()
        self.assertEqual(dialog.pending(), "#ff0000")
        self.assertEqual(dialog._hex.text(), "#ff0000")
        self.assertEqual(dialog._hsv_fields[1].value(), 100)

    def test_custom_dialog_hsv_fields_drive_color(self):
        dialog = CustomColorDialog("#0078d4")
        for field, number in zip(dialog._hsv_fields, (120, 100, 100)):
            field.set_value(number)
        dialog._on_hsv()
        self.assertEqual(dialog.pending(), "#00ff00")
        self.assertEqual([f.value() for f in dialog._rgb], [0, 255, 0])

    def test_custom_dialog_ignores_empty_field(self):
        dialog = CustomColorDialog("#0078d4")
        dialog._rgb[0].edit.setText("")
        dialog._on_rgb()
        self.assertEqual(dialog.pending(), "#0078d4")

    def test_custom_dialog_pairs_beside_anchor(self):
        anchor = AccentDialog()
        anchor.move(300, 200)
        dialog = CustomColorDialog("#0078d4", anchor)
        origin, anchor_target, own_target = dialog._plan
        self.assertEqual(origin, anchor.pos())
        card_width = anchor.width() - 2 * anchor.MARGIN
        self.assertEqual(own_target.x() - anchor_target.x(), card_width + PAIR_GAP)
        self.assertEqual(dialog.windowOpacity(), 0.0)

    def test_custom_dialog_progress_moves_both_windows(self):
        anchor = AccentDialog()
        anchor.move(300, 200)
        dialog = CustomColorDialog("#0078d4", anchor)
        origin, anchor_target, own_target = dialog._plan
        dialog._apply_progress(1.0)
        self.assertEqual(anchor.pos(), anchor_target)
        self.assertEqual(dialog.pos(), own_target)
        self.assertEqual(dialog.windowOpacity(), 1.0)
        dialog._apply_progress(0.0)
        self.assertEqual(anchor.pos(), origin)

    def test_custom_dialog_without_anchor_has_no_plan(self):
        self.assertIsNone(CustomColorDialog("#0078d4")._plan)

    def test_custom_dialog_closes_immediately_when_hidden(self):
        anchor = AccentDialog()
        dialog = CustomColorDialog("#0078d4", anchor)
        dialog.accept()
        self.assertEqual(dialog.result(), CustomColorDialog.DialogCode.Accepted)

    def test_custom_dialog_more_reveals_fields(self):
        dialog = CustomColorDialog("#0078d4")
        self.assertTrue(dialog._fields.isHidden())
        dialog._more.setChecked(True)
        self.assertFalse(dialog._fields.isHidden())

    def test_rgb_helpers_round_trip(self):
        self.assertEqual(accent.to_rgb("#336699"), (51, 102, 153))
        self.assertEqual(accent.from_rgb(51, 102, 153), "#336699")
        self.assertEqual(accent.from_rgb(300, -4, 0), "#ff0000")

    def test_preset_choice_is_not_saved_as_custom(self):
        accent.choose(accent.PRESETS[0][1])
        self.assertEqual(accent.custom(), [])


if __name__ == "__main__":
    unittest.main()