import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - GUI tests need PySide6
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from npigallery.gui.theme import theme

        cls.app = QApplication.instance() or QApplication([])
        theme.apply_theme(cls.app, "dark")

    def test_message_dialog_has_ok_and_fits_text(self):
        from npigallery.gui.common.dialogs import MessageDialog

        dialog = MessageDialog("Title", "Hello\nworld")
        dialog.show()
        self.assertGreater(dialog.height(), 100)
        self.assertEqual(dialog.windowTitle(), "Title")
        dialog.close()

    def test_long_message_scrolls_instead_of_growing(self):
        from npigallery.gui.common.dialogs import MessageDialog

        dialog = MessageDialog("Many", "\n".join(f"line {i}" for i in range(200)))
        dialog.show()
        self.assertLess(dialog.height(), 480)
        dialog.close()

    def test_batch_dialog_toggles_output_row(self):
        from npigallery.gui.batch.dialog import BatchDialog, BatchMode

        dialog = BatchDialog(BatchMode.DUMP, None)
        dialog.show()
        self.assertTrue(dialog._output_row.isHidden())
        collapsed = dialog.height()
        dialog._custom_output.setChecked(True)
        self.assertFalse(dialog._output_row.isHidden())
        self.assertGreater(dialog.height(), collapsed)
        dialog.close()

    def test_batch_stats_text_matches_design(self):
        from npigallery.gui.batch.dialog import BatchDialog, BatchMode

        dialog = BatchDialog(BatchMode.DUMP, None)
        dialog._on_progress(482, 1284)
        dialog._on_stats(480, 2, 0)
        self.assertEqual(dialog._line_done.text(), "Packed 482 of 1,284")
        self.assertEqual(dialog._line_ok.text(), "Verified 480")
        self.assertEqual(dialog._line_skipped.text(), "Skipped 2 (already .npi)")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
