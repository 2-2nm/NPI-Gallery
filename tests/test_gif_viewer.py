import io
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

try:
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
    from PySide6.QtWidgets import QApplication

    from npigallery.gui.viewer.canvas import ImageCanvas
    from npigallery.gui.viewer.loader import decode
    from npigallery.services.scanner import MediaItem

    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def _gif_bytes(frames: int) -> bytes:
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    images = [Image.new("RGB", (40, 30), colours[i % 4]) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, "GIF", save_all=True, append_images=images[1:], duration=30, loop=0)
    return buffer.getvalue()


def _item(path: Path):
    import inspect

    params = inspect.signature(MediaItem).parameters
    kwargs = {}
    for name, param in params.items():
        if param.default is inspect.Parameter.empty:
            kwargs[name] = {"path": path, "name": path.name}.get(name, 0)
    return MediaItem(**kwargs)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AnimatedGifTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _decode(self, data: bytes):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.gif"
            path.write_bytes(data)
            return decode(_item(path), with_location=False)

    def test_animated_gif_keeps_its_bytes(self):
        data = _gif_bytes(3)
        view = self._decode(data)
        self.assertEqual(view.animation, data)
        self.assertEqual((view.width, view.height), (40, 30))

    def test_single_frame_gif_is_a_still(self):
        view = self._decode(_gif_bytes(1))
        self.assertIsNone(view.animation)

    def test_canvas_plays_the_frames(self):
        data = _gif_bytes(3)
        view = self._decode(data)
        canvas = ImageCanvas()
        canvas.resize(200, 200)
        canvas.set_image(view.image)
        canvas.play_animation(view.animation)
        self.assertTrue(canvas.is_animating())
        seen = set()
        clock = QElapsedTimer()
        clock.start()
        while clock.elapsed() < 600 and len(seen) < 3:
            QCoreApplication.processEvents()
            pixmap = canvas._item.pixmap().toImage()
            if not pixmap.isNull():
                seen.add(pixmap.pixel(5, 5))
        self.assertGreaterEqual(len(seen), 2, "the canvas never advanced past the first frame")
        canvas.clear_image()
        self.assertFalse(canvas.is_animating())


if __name__ == "__main__":
    unittest.main()