"""Where a photo goes on a sheet of paper. No Qt: the print dialog's preview and the printer use the same numbers.

All lengths on paper are millimetres, the source rectangle is in pixels of the photo.
"""

from __future__ import annotations

from dataclasses import dataclass

MM_PER_INCH = 25.4

# // key -> (label, (short side, long side) in mm); "page" is the whole sheet inside the margins
SIZES: tuple[tuple[str, str, tuple[float, float] | None], ...] = (
    ("page", "Full page", None),
    ("10x15", "10 × 15 cm", (100.0, 150.0)),
    ("13x18", "13 × 18 cm", (130.0, 180.0)),
    ("20x25", "20 × 25 cm", (200.0, 250.0)),
)
MARGINS: tuple[tuple[str, str, float], ...] = (
    ("none", "None", 0.0),
    ("small", "Small", 6.0),
    ("normal", "Normal", 12.7),
)
ORIENTATIONS: tuple[tuple[str, str], ...] = (("auto", "Match the photo"), ("portrait", "Portrait"), ("landscape", "Landscape"))

GOOD_DPI = 200  # at or above this a print looks sharp
LOW_DPI = 120  # below this the pixels are visible


@dataclass(frozen=True)
class PrintOptions:
    size: str = "page"
    margin: str = "small"
    orientation: str = "auto"
    fill: bool = False  # crop the photo to fill the frame instead of fitting it inside


@dataclass(frozen=True)
class Placement:
    frame: tuple[float, float, float, float]  # x, y, width, height on the paper, mm
    source: tuple[float, float, float, float]  # x, y, width, height of the part of the photo that is printed, px

    @property
    def frame_width_mm(self) -> float:
        return self.frame[2]


def is_landscape(orientation: str, image_width: int, image_height: int) -> bool:
    if orientation == "landscape":
        return True
    if orientation == "portrait":
        return False
    return image_width > image_height  # "auto": a square photo is printed upright


def sheet_size(paper_mm: tuple[float, float], landscape: bool) -> tuple[float, float]:
    """(width, height) of the sheet as it comes out of the printer, from the paper's portrait size."""
    short, long = sorted(paper_mm)
    return (long, short) if landscape else (short, long)


def margin_mm(key: str) -> float:
    return next((value for k, _label, value in MARGINS if k == key), 0.0)


def place(sheet: tuple[float, float], image: tuple[int, int], options: PrintOptions) -> Placement:
    """The frame the photo is printed in, and which part of the photo goes into it."""
    page_w, page_h = sheet
    image_w, image_h = max(1, image[0]), max(1, image[1])
    margin = min(margin_mm(options.margin), min(page_w, page_h) / 4)
    area_x, area_y = margin, margin
    area_w, area_h = max(1.0, page_w - 2 * margin), max(1.0, page_h - 2 * margin)

    fixed = next((box for key, _label, box in SIZES if key == options.size), None)
    if fixed is None:
        box_w, box_h = area_w, area_h
    else:
        short, long = fixed
        box_w, box_h = (long, short) if page_w > page_h else (short, long)
        shrink = min(1.0, area_w / box_w, area_h / box_h)  # a 20 x 25 cm frame on an A5 sheet is made to fit
        box_w, box_h = box_w * shrink, box_h * shrink
    box_x, box_y = area_x + (area_w - box_w) / 2, area_y + (area_h - box_h) / 2

    if options.fill:
        frame = (box_x, box_y, box_w, box_h)
        photo_aspect, box_aspect = image_w / image_h, box_w / box_h
        if photo_aspect > box_aspect:  # the photo is wider than the frame: cut the sides
            width = image_h * box_aspect
            source = ((image_w - width) / 2, 0.0, width, float(image_h))
        else:
            height = image_w / box_aspect
            source = (0.0, (image_h - height) / 2, float(image_w), height)
        return Placement(frame, source)

    scale = min(box_w / image_w, box_h / image_h)
    width, height = image_w * scale, image_h * scale
    frame = (box_x + (box_w - width) / 2, box_y + (box_h - height) / 2, width, height)
    return Placement(frame, (0.0, 0.0, float(image_w), float(image_h)))


def effective_dpi(placement: Placement) -> float:
    """Pixels of the photo per inch of paper."""
    inches = placement.frame[2] / MM_PER_INCH
    return placement.source[2] / inches if inches > 0 else 0.0


def quality_label(dpi: float) -> str:
    if dpi >= GOOD_DPI:
        return "sharp"
    if dpi >= LOW_DPI:
        return "fine for casual prints"
    return "low: pixels may show"
