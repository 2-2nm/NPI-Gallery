from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

from .. import profiling
from ..config import APP_NAME, APP_TITLE, thumbnail_database_path
from ..services.thumbcache import ThumbnailCache
from .common.branding import app_icon, set_app_user_model_id
from .common.hwdecode import configure as configure_hardware_decoding
from .common.winperf import apply as apply_timer_tweaks
from .gallery.view import GalleryView
from .main_window.window import MainWindow
from .quick_open import QuickOpen, Receiver, hand_over, media_arguments
from .theme.preferences import prefs
from .theme.theme import apply_theme


class _TooltipFilter(QObject):
    """No hover pop-ups on buttons, menus and the sidebar; photo tiles keep their file info."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            return not isinstance(watched.parent(), GalleryView) if hasattr(watched, "parent") else True
        return False


def run(argv: list[str]) -> int:
    set_app_user_model_id()
    apply_timer_tweaks()  # 1 ms timers, like VLC: frames are not dropped for a coarse clock
    configure_hardware_decoding()  # before QtMultimedia starts: video decodes on the GPU
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    app.setWindowIcon(app_icon())
    app.setOrganizationName(APP_NAME)
    media_paths = media_arguments(argv)
    if media_paths and hand_over(media_paths):
        return 0
    receiver = Receiver(app)
    if media_paths and not receiver.listening and hand_over(media_paths):
        return 0
    apply_theme(app, prefs().get("theme"))
    tooltip_filter = _TooltipFilter(app)
    app.installEventFilter(tooltip_filter)
    quick_open = QuickOpen(app)
    receiver.received.connect(quick_open.open_paths)
    if media_paths:
        quick_open.open_paths(media_paths)
        return app.exec() if quick_open.has_viewers() else 0
    start_folder = None
    if len(argv) > 1 and Path(argv[1]).is_dir():
        start_folder = Path(argv[1])
    profiling.mark("QApplication + theme ready")
    cache = ThumbnailCache(thumbnail_database_path())
    profiling.mark("thumbnail cache opened")
    with profiling.span("MainWindow constructed"):
        window = MainWindow(cache, start_folder)
    with profiling.span("window.show()"):
        window.show()
    profiling.install_monitors(app)
    profiling.mark("entering event loop")
    return app.exec()