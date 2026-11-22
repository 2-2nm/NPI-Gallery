"""The viewer's Print dialog: a live preview of the sheet, the usual choices and the printer's own settings.

The native Windows dialog (QPrintDialog) cannot show a preview for an app that is not a UWP / Win32 print-preview
client ("This app doesn't support print preview"), so the choices that matter for a photo are asked here instead:
printer, paper, orientation, size on the sheet, margins, fit or fill, copies and black & white. "More settings..."
still opens the native dialog for what only the driver knows (quality, paper tray, ...). The preview and the
printer use the same numbers (`services.printing.place`), so what the sheet shows is what comes out.
"""

from __future__ import annotations

from PySide6.QtCore import QMarginsF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ...services import printing as pr
from ..common.dialogs import DialogCheck, FloatDialog, button_row, dialog_label, make_button
from ..theme import theme

PREVIEW_SIDE = 960  # the photo shown in the sheet is shrunk to this many pixels on its long side
SHEET_WIDTH = 330
SHEET_HEIGHT = 400
CONTROLS_WIDTH = 330
COMMON_PAPER = (
    QPageSize.PageSizeId.A4,
    QPageSize.PageSizeId.Letter,
    QPageSize.PageSizeId.A5,
    QPageSize.PageSizeId.A3,
    QPageSize.PageSizeId.A6,
    QPageSize.PageSizeId.Legal,
    QPageSize.PageSizeId.Tabloid,
    QPageSize.PageSizeId.B5,
    QPageSize.PageSizeId.B4,
    QPageSize.PageSizeId.Executive,
)


def flattened(image: QImage, black_and_white: bool) -> QImage:
    """Transparent areas print white, not black; optionally in grey."""
    if image.hasAlphaChannel():
        flat = QImage(image.size(), QImage.Format.Format_RGB32)
        flat.fill(Qt.GlobalColor.white)
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    if black_and_white:
        image = image.convertToFormat(QImage.Format.Format_Grayscale8)
    return image


class _Sheet(QWidget):
    """The paper with the photo on it, drawn to scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SHEET_WIDTH, SHEET_HEIGHT)
        self._sheet_mm = (210.0, 297.0)
        self._placement: pr.Placement | None = None
        self._image: QImage | None = None
        self._photo_size = (1, 1)  # size of the real photo, for scaling the source rectangle to the preview image

    def set_photo(self, preview: QImage, photo_size: tuple[int, int]) -> None:
        self._image, self._photo_size = preview, photo_size
        self.update()

    def set_layout(self, sheet_mm: tuple[float, float], placement: pr.Placement) -> None:
        self._sheet_mm, self._placement = sheet_mm, placement
        self.update()

    def paintEvent(self, event) -> None:
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.thumb_bg))
        painter.drawRoundedRect(self.rect(), 10, 10)
        if self._placement is None:
            return
        page_w, page_h = self._sheet_mm
        room = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        scale = min(room.width() / page_w, room.height() / page_h)
        sheet = QRectF(0, 0, page_w * scale, page_h * scale)
        sheet.moveCenter(room.center())
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRect(sheet.translated(2, 3))  # a soft shadow under the paper
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(sheet)
        fx, fy, fw, fh = self._placement.frame
        frame = QRectF(sheet.x() + fx * scale, sheet.y() + fy * scale, fw * scale, fh * scale)
        if self._image is not None and not self._image.isNull():
            ratio = self._image.width() / max(1, self._photo_size[0])
            sx, sy, sw, sh = self._placement.source
            painter.drawImage(frame, self._image, QRectF(sx * ratio, sy * ratio, sw * ratio, sh * ratio))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.drawRect(frame)


class PrintDialog(FloatDialog):
    def __init__(self, image: QImage, name: str, parent=None):
        super().__init__("Print", parent, width=CONTROLS_WIDTH + SHEET_WIDTH + 60)
        from ..settings.page import Dropdown  # the app's own select box (imported late: the settings page is large)

        self._image = image
        self._name = name
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        self._copies = 1
        self._pages: list[QPageSize] = []
        self._options = pr.PrintOptions()

        names = QPrinterInfo.availablePrinterNames()
        default = QPrinterInfo.defaultPrinterName()
        chosen = default if default in names else (names[0] if names else "")
        self._printer_menu = Dropdown([(n, n) for n in names] or [("No printer found", "")], chosen)
        self._paper_menu = Dropdown([("A4", "0")], "0")
        self._orientation_menu = Dropdown([(label, key) for key, label in pr.ORIENTATIONS], self._options.orientation)
        self._size_menu = Dropdown([(label, key) for key, label, _box in pr.SIZES], self._options.size)
        self._margin_menu = Dropdown([(label, key) for key, label, _mm in pr.MARGINS], self._options.margin)
        self._fill = DialogCheck("Fill the frame (crops the edges)")
        self._grey = DialogCheck("Black and white")
        self._copies_label = dialog_label("1")
        self._copies_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._copies_label.setFixedWidth(40)
        less, more = make_button("", icon_name="minus"), make_button("", icon_name="plus")
        for button in (less, more):
            button.setFixedWidth(40)
            button.setStyleSheet("padding: 0px;")  # the usual 20 px side padding would leave no room for the icon
            button.setAutoDefault(False)
            button.setDefault(False)
        less.clicked.connect(lambda: self._set_copies(self._copies - 1))
        more.clicked.connect(lambda: self._set_copies(self._copies + 1))
        copies_row = QHBoxLayout()
        copies_row.setContentsMargins(0, 0, 0, 0)
        copies_row.setSpacing(6)
        copies_row.addWidget(less)
        copies_row.addWidget(self._copies_label)
        copies_row.addWidget(more)
        copies_row.addStretch(1)

        for menu in (self._printer_menu, self._paper_menu, self._orientation_menu, self._size_menu, self._margin_menu):
            menu.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            menu.setMinimumWidth(0)
            menu.setFixedHeight(34)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)
        for label, widget in (
            ("Printer", self._printer_menu),
            ("Paper", self._paper_menu),
            ("Orientation", self._orientation_menu),
            ("Photo size", self._size_menu),
            ("Margins", self._margin_menu),
        ):
            controls.addLayout(self._field(label, widget))
        controls.addWidget(self._fill)
        controls.addWidget(self._grey)
        controls.addLayout(self._field("Copies", copies_row))
        controls.addStretch(1)
        controls_box = QWidget()
        controls_box.setFixedWidth(CONTROLS_WIDTH)
        controls_box.setLayout(controls)

        self._sheet = _Sheet()
        self._quality = dialog_label("", "detail", wrap=True)
        self._quality.setFixedWidth(SHEET_WIDTH)
        self._quality.setMinimumHeight(self._quality.fontMetrics().height() * 2 + 4)
        self._quality.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        preview = QVBoxLayout()
        preview.setContentsMargins(0, 0, 0, 0)
        preview.setSpacing(8)
        preview.addWidget(self._sheet)
        preview.addWidget(self._quality)
        preview.addStretch(1)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(20)
        columns.addWidget(controls_box)
        columns.addLayout(preview)
        self.body.addLayout(columns)

        self._more = make_button("More settings…")
        self._more.setAutoDefault(False)
        self._more.setDefault(False)
        self._more.clicked.connect(self._native_settings)
        cancel = make_button("Cancel")
        cancel.clicked.connect(self.reject)
        self._print_button = make_button("Print", "primary", "print")
        self._print_button.clicked.connect(self._print)
        self._print_button.setEnabled(bool(names))
        footer = button_row(cancel, self._print_button)
        footer.insertWidget(0, self._more)
        self.body.addSpacing(4)
        self.body.addLayout(footer)

        self._preview_image = self._make_preview()
        self._sheet.set_photo(self._preview_image, (image.width(), image.height()))
        self._load_papers()
        self._printer_menu.value_changed.connect(lambda _key: self._load_papers())
        for menu in (self._paper_menu, self._orientation_menu, self._size_menu, self._margin_menu):
            menu.value_changed.connect(lambda _key: self._refresh())
        self._fill.toggled.connect(lambda _on: self._refresh())
        self._grey.toggled.connect(lambda _on: self._refresh())
        self.finish()
        self._refresh()

    # // building

    @staticmethod
    def _field(label: str, control) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(dialog_label(label, "name"))
        if isinstance(control, QHBoxLayout):
            column.addLayout(control)
        else:
            column.addWidget(control)
        return column

    def _make_preview(self) -> QImage:
        image = self._image
        if max(image.width(), image.height()) > PREVIEW_SIDE:
            image = image.scaled(
                QSize(PREVIEW_SIDE, PREVIEW_SIDE), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        return flattened(image, False)

    def _load_papers(self) -> None:
        """The paper sizes of the chosen printer: the common ones first, the printer's own default selected."""
        name = self._printer_menu.value()
        sizes: list[QPageSize] = []
        default_id = None
        if name:
            info = QPrinterInfo.printerInfo(name)
            supported = info.supportedPageSizes()
            sizes = [size for size in supported if size.id() in COMMON_PAPER]
            sizes.sort(key=lambda size: COMMON_PAPER.index(size.id()))
            if not sizes:
                sizes = list(supported)[:12]
            default_id = info.defaultPageSize().id()
        if not sizes:
            sizes = [QPageSize(QPageSize.PageSizeId.A4)]
        self._pages = sizes
        wanted = next((i for i, size in enumerate(sizes) if size.id() == default_id), 0)
        self._paper_menu.set_options([(size.name(), str(i)) for i, size in enumerate(sizes)], str(wanted))
        self._refresh()

    def _set_copies(self, value: int) -> None:
        self._copies = max(1, min(99, value))
        self._copies_label.setText(str(self._copies))

    # // the sheet

    def _paper_mm(self) -> tuple[float, float]:
        index = int(self._paper_menu.value() or 0)
        size = self._pages[index] if 0 <= index < len(self._pages) else QPageSize(QPageSize.PageSizeId.A4)
        mm = size.size(QPageSize.Unit.Millimeter)
        return mm.width(), mm.height()

    def _current_options(self) -> pr.PrintOptions:
        return pr.PrintOptions(
            size=self._size_menu.value(),
            margin=self._margin_menu.value(),
            orientation=self._orientation_menu.value(),
            fill=self._fill.isChecked(),
        )

    def _layout(self) -> tuple[tuple[float, float], bool, pr.Placement]:
        options = self._current_options()
        landscape = pr.is_landscape(options.orientation, self._image.width(), self._image.height())
        sheet = pr.sheet_size(self._paper_mm(), landscape)
        return sheet, landscape, pr.place(sheet, (self._image.width(), self._image.height()), options)

    def _refresh(self) -> None:
        if not hasattr(self, "_preview_image"):
            return  # still being built
        sheet, _landscape, placement = self._layout()
        grey = self._grey.isChecked()
        shown = flattened(self._preview_image, True) if grey else self._preview_image
        self._sheet.set_photo(shown, (self._image.width(), self._image.height()))
        self._sheet.set_layout(sheet, placement)
        dpi = pr.effective_dpi(placement)
        width_cm = placement.frame[2] / 10
        height_cm = placement.frame[3] / 10
        self._quality.setText(f"{width_cm:.1f} × {height_cm:.1f} cm at about {dpi:.0f} dpi: {pr.quality_label(dpi)}")

    # // printing

    def _apply_to_printer(self) -> tuple[tuple[float, float], pr.Placement]:
        sheet, landscape, placement = self._layout()
        printer = self._printer
        name = self._printer_menu.value()
        if name:
            printer.setPrinterName(name)
        index = int(self._paper_menu.value() or 0)
        if 0 <= index < len(self._pages):
            printer.setPageSize(self._pages[index])
        printer.setPageOrientation(QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait)
        printer.setFullPage(True)  # the margins are ours; the whole sheet is the canvas
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        printer.setCopyCount(self._copies)
        printer.setColorMode(QPrinter.ColorMode.GrayScale if self._grey.isChecked() else QPrinter.ColorMode.Color)
        printer.setDocName(self._name)
        return sheet, placement

    def _print(self) -> None:
        _sheet, placement = self._apply_to_printer()
        picture = flattened(self._image, self._grey.isChecked())
        painter = QPainter()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if not painter.begin(self._printer):
                return  # also what a cancelled "Save as" of Print to PDF looks like: stay here, nothing was printed
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            per_mm = self._printer.resolution() / pr.MM_PER_INCH
            x, y, w, h = placement.frame
            sx, sy, sw, sh = placement.source
            painter.drawImage(QRectF(x * per_mm, y * per_mm, w * per_mm, h * per_mm), picture, QRectF(sx, sy, sw, sh))
            painter.end()
        finally:
            QApplication.restoreOverrideCursor()
        self.accept()

    def _native_settings(self) -> None:
        """The driver's own settings (quality, tray, ...); what it changes that this dialog shows is read back."""
        self._apply_to_printer()
        dialog = QPrintDialog(self._printer, self)
        if dialog.exec() not in (1, QDialog.DialogCode.Accepted):
            return
        printer_name = self._printer.printerName()
        if printer_name and printer_name != self._printer_menu.value() and QPrinterInfo.printerInfo(printer_name).printerName():
            self._printer_menu.set_value(printer_name)
            self._load_papers()
        self._set_copies(self._printer.copyCount())
        orientation = "landscape" if self._printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape else "portrait"
        self._orientation_menu.set_value(orientation)
        self._refresh()
