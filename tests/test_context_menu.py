import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidgetAction

    from npigallery.gui.main_window.window import MainWindow
    from npigallery.gui.common.motion import PHOTO_MENU_DURATION, PHOTO_MENU_SLIDE, opens_upward
    from npigallery.services.scanner import scan_folder
    from npigallery.services.thumbcache import ThumbnailCache

    HAS_PYSIDE = True
except Exception:
    HAS_PYSIDE = False


@unittest.skipUnless(HAS_PYSIDE, "PySide6 not installed")
class PhotoContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.photo = self.root / "sample.jpg"
        Image.new("RGB", (120, 80), (40, 120, 200)).save(self.photo, "JPEG")
        self.cache = ThumbnailCache(self.root / "thumbs.db")
        self.window = MainWindow(self.cache, start_folder=self.root)
        self.window._stop_scan()
        items = scan_folder(self.root)
        self.window._known = items
        self.window._model.set_items(items)
        self.window.resize(1000, 700)
        self.window._view.resize(800, 600)
        self.window._view._relayout(keep_scroll=False)

    def tearDown(self):
        self.window.close()
        self.cache.close()
        self._tmp.cleanup()

    def test_context_menu_options_and_excluded_items(self):
        items = self.window._model.items()
        self.assertEqual(len(items), 1)
        menu = self.window.build_photo_context_menu(0, items)
        self.addCleanup(menu.deleteLater)

        self.assertEqual(menu.animation_duration, PHOTO_MENU_DURATION)
        self.assertEqual(menu.slide_distance, PHOTO_MENU_SLIDE)
        self.assertGreater(menu.slide_distance, 0)

        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(
            labels,
            [
                "Open",
                "Edit",
                "Start slideshow",
                "Copy",
                "Copy as path",
                "Share",
                "Move/Copy",
                "Add to album",
                "Rename",
                "Open in File Explorer",
                "Go to folder in NPI Gallery",
                "Delete",
            ],
        )

        excluded = {
            "Print",
            "Backup to OneDrive",
            "Create a video",
            "View on OneDrive online",
            "View in OneDrive online",
        }
        self.assertTrue(excluded.isdisjoint(labels))

        move_copy_action = next(a for a in menu.actions() if a.text() == "Move/Copy")
        submenu = move_copy_action.menu()
        self.assertIsNotNone(submenu)
        sub_labels = [a.text() for a in submenu.actions() if not a.isSeparator()]
        self.assertEqual(sub_labels, ["Copy to folder", "Move to folder"])

    def test_delete_row_is_a_regular_menu_item(self):
        # A custom widget row used its own hard-coded paddings and drifted off the icon / label /
        # shortcut columns. Delete must be a plain QAction so it shares the exact same grid.
        menu = self.window.build_photo_context_menu(0, self.window._model.items())
        self.addCleanup(menu.deleteLater)

        delete = next(a for a in menu.actions() if a.text() == "Delete")
        self.assertNotIsInstance(delete, QWidgetAction)
        self.assertFalse(delete.icon().isNull())
        self.assertEqual(delete.shortcut().toString(), "Del")
        self.assertIs(menu.defaultAction(), delete)  # stylesheet paints QMenu::item:default red

        self.assertTrue(all(not isinstance(a, QWidgetAction) for a in menu.actions()))

    def test_shortcuts_work_while_the_menu_is_open(self):
        menu = self.window.build_photo_context_menu(0, self.window._model.items())
        self.addCleanup(menu.deleteLater)
        calls = []
        copy = next(a for a in menu.actions() if a.text() == "Copy")
        copy.triggered.connect(lambda *_: calls.append("copy"))

        menu.popup(QPoint(120, 140))
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        menu.keyPressEvent(event)
        self.assertEqual(calls, ["copy"])
        self.assertFalse(menu.isVisible())

        # modifier-only presses and unbound keys must not trigger anything
        menu.popup(QPoint(120, 140))
        menu.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier))
        self.assertEqual(calls, ["copy"])
        self.assertTrue(menu.isVisible())

    def test_enter_opens_on_first_press(self):
        menu = self.window.build_photo_context_menu(0, self.window._model.items())
        self.addCleanup(menu.deleteLater)
        calls = []
        open_action = next(a for a in menu.actions() if a.text() == "Open")
        open_action.triggered.connect(lambda *_: calls.append("open"))
        self.assertEqual(open_action.shortcut().toString(), "Enter")

        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            calls.clear()
            menu.popup(QPoint(120, 140))
            menu.setActiveAction(None)
            menu.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
            self.assertEqual(calls, ["open"])

        # a row highlighted with the arrows keeps priority over Open
        menu.popup(QPoint(120, 140))
        copy = next(a for a in menu.actions() if a.text() == "Copy")
        menu.setActiveAction(copy)
        calls.clear()
        menu.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(calls, [])

    def test_flicker_free_menu_transition_lifecycle(self):
        items = self.window._model.items()
        menu = self.window.build_photo_context_menu(0, items)
        self.addCleanup(menu.deleteLater)

        menu.popup(QPoint(120, 140))
        self.assertTrue(menu.is_animating)
        self.assertIsNotNone(menu._snapshot)
        self.assertFalse(menu._snapshot.isNull())
        self.assertAlmostEqual(menu._progress, 0.0, places=3)

        menu._on_step(0.5)
        self.assertAlmostEqual(menu._progress, 0.5, places=3)

        menu._finish_animation()
        self.assertFalse(menu.is_animating)
        self.assertIsNone(menu._snapshot)
        self.assertAlmostEqual(menu._progress, 1.0, places=3)
        menu.hide()

    def test_menu_direction_follows_where_it_opened_from(self):
        # pointer at/above the top edge: drops down; pointer at the bottom edge: rises up
        self.assertFalse(opens_upward(top=100, height=200, pointer_y=100))
        self.assertFalse(opens_upward(top=100, height=200, pointer_y=60))
        self.assertTrue(opens_upward(top=100, height=200, pointer_y=300))
        self.assertTrue(opens_upward(top=100, height=200, pointer_y=320))
        self.assertFalse(opens_upward(top=100, height=200, pointer_y=900))  # unrelated pointer

    def test_toolbar_menus_open_centred_under_their_button(self):
        from PySide6.QtWidgets import QToolButton

        self.window.show()
        self.window.resize(1200, 800)
        QApplication.processEvents()
        buttons = [b for b in self.window._toolbar_pill.findChildren(QToolButton) if b.menu() is not None]
        self.assertGreaterEqual(len(buttons), 4)  # sort, filter, thumbnail size, more
        for button in buttons:
            if not button.isVisible():
                continue
            menu = button.menu()
            menu.ensurePolished()
            menu.adjustSize()
            below = button.mapToGlobal(QPoint(0, button.height() + 4))  # where Qt would put it: left-aligned
            position, _upward = menu._resolve_placement(below, menu.size(), below)
            middle = button.mapToGlobal(QPoint(button.width() // 2, 0)).x()
            # centred unless the window edge forced it inward
            if position.x() > self.window.frameGeometry().left() + 12 and (
                position.x() + menu.width() < self.window.frameGeometry().right() - 12
            ):
                self.assertAlmostEqual(position.x() + menu.width() / 2, middle, delta=1.5)

    def test_menu_near_window_bottom_opens_upward_from_the_pointer(self):
        items = self.window._model.items()
        menu = self.window.build_photo_context_menu(0, items)
        self.addCleanup(menu.deleteLater)
        menu.ensurePolished()
        menu.adjustSize()
        self.window.show()
        bottom = self.window.frameGeometry().bottom()
        pointer = QPoint(self.window.frameGeometry().left() + 200, bottom - 40)
        position, upward = menu._resolve_placement(pointer, menu.size(), pointer)
        self.assertTrue(upward)
        self.assertLessEqual(position.y() + menu.height(), pointer.y() + 1)

        top_pointer = QPoint(pointer.x(), self.window.frameGeometry().top() + 60)
        position, upward = menu._resolve_placement(top_pointer, menu.size(), top_pointer)
        self.assertFalse(upward)

    def test_click_during_animation_completes_transition_immediately(self):
        items = self.window._model.items()
        menu = self.window.build_photo_context_menu(0, items)
        self.addCleanup(menu.deleteLater)

        menu.popup(QPoint(120, 140))
        self.assertTrue(menu.is_animating)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(menu.pos() + QPoint(10, 10)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(menu, press)
        self.assertFalse(menu.is_animating)
        menu.hide()

    def test_copy_as_path_and_right_click_hit_testing(self):
        items = self.window._model.items()
        self.window._copy_paths(items)
        self.assertEqual(QApplication.clipboard().text(), f'"{self.photo}"')

        triggered = []
        self.window._view.context_menu_requested.connect(lambda row, pos: triggered.append((row, pos)))
        tile_center = self.window._view._viewport_rect(0).center()
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            tile_center,
            self.window._view.viewport().mapToGlobal(tile_center),
        )
        self.window._view.contextMenuEvent(event)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0][0], 0)


if __name__ == "__main__":
    unittest.main()