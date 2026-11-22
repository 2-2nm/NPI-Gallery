"""Non-destructive description of an edit (crop, straighten, rotate, flip, adjustments, filter) and the code that applies it.

An `EditState` is cheap to copy, so the editor's undo history is just a list of them. The pixels are only
touched by `render`, which the preview and the final save share: what the person sees is what is written.

Geometry: the "working picture" W is the photo after the 90-degree turns and flips in `ops`. The straighten
`angle` (clockwise, degrees) turns W about its centre, and `crop` is an axis-aligned rectangle in W's pixel
coordinates that must lie inside that turned picture, so the result never has empty corners. The
`filter` (see `services.filters`) and then the `adjust` sliders (see `services.adjust`) are applied last, to the
cropped result.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin

from ..filesystem import unique_path, write_atomic
from ..npi import build_thumbnail, detect_format, inspect_image, is_npi_path, read_record
from ..npi.container import read_payload, write_npi
from ..npi.formats import ImageFormat
from .adjust import Adjustments, apply_adjustments
from .filters import FilterChoice, apply_filter
from .rotate import JPEG_QUALITY, SUPPORTED, RotateError, _fix_exif, _rewrite

MAX_ANGLE = 45.0
MIN_SIDE = 16.0
EDITED_SUFFIX = " (edited)"

Rect = tuple[float, float, float, float]  # x, y, width, height
Size = tuple[float, float]

_TRANSPOSE = {
    "cw": Image.Transpose.ROTATE_270,
    "ccw": Image.Transpose.ROTATE_90,
    "fh": Image.Transpose.FLIP_LEFT_RIGHT,
    "fv": Image.Transpose.FLIP_TOP_BOTTOM,
}


@dataclass(frozen=True)
class EditState:
    ops: tuple[str, ...] = ()  # "cw" / "ccw" (90 degrees), "fh" / "fv" (flip), applied in order
    angle: float = 0.0
    crop: Rect | None = None  # in working-picture pixels; None = everything
    adjust: Adjustments = Adjustments()  # the Adjustment tool's sliders
    filter: FilterChoice = FilterChoice()  # the Filter tool's look and its intensity

    def is_identity(self, size: Size) -> bool:
        """True when applying this state would give the photo back unchanged."""
        if abs(self.angle) > 1e-6 or not self.adjust.is_identity() or not self.filter.is_identity():
            return False
        if self.ops and not _ops_cancel(self.ops):
            return False
        if self.crop is None:
            return True
        w, h = working_size(size, self.ops)
        x, y, cw, ch = self.crop
        return abs(x) < 0.5 and abs(y) < 0.5 and abs(cw - w) < 0.5 and abs(ch - h) < 0.5


def _ops_cancel(ops: tuple[str, ...]) -> bool:
    """Do the ops add up to nothing? (Checked on a tiny asymmetric picture.)"""
    probe = Image.new("L", (2, 3))
    probe.putpixel((0, 0), 255)
    result = apply_ops(probe, ops)
    return result.size == (2, 3) and result.getpixel((0, 0)) == 255


def working_size(size: Size, ops: tuple[str, ...]) -> Size:
    w, h = size
    for op in ops:
        if op in ("cw", "ccw"):
            w, h = h, w
    return w, h


def apply_ops(image: Image.Image, ops: tuple[str, ...]) -> Image.Image:
    for op in ops:
        image = image.transpose(_TRANSPOSE[op])
    return image


def op_state(state: EditState, size: Size, op: str) -> EditState:
    """`state` after one more op. `size` is the working size *before* the op; the crop and angle follow."""
    w, h = size
    crop = state.crop
    angle = state.angle
    if crop is not None:
        x, y, cw, ch = crop
        if op == "cw":
            crop = (h - (y + ch), x, ch, cw)
        elif op == "ccw":
            crop = (y, w - (x + cw), ch, cw)
        elif op == "fh":
            crop = (w - (x + cw), y, cw, ch)
        elif op == "fv":
            crop = (x, h - (y + ch), cw, ch)
    if op in ("fh", "fv"):
        angle = -angle  # a mirror turns the tilt the other way
    return replace(state, ops=state.ops + (op,), crop=crop, angle=angle)


# // rectangle geometry (pure maths, no Qt)


def _to_source(angle: float, size: Size):
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    cx, cy = size[0] / 2, size[1] / 2

    def convert(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - cx, y - cy
        return cx + c * dx + s * dy, cy - s * dx + c * dy

    return convert, c, s


def rect_valid(rect: Rect, size: Size, angle: float, eps: float = 0.5) -> bool:
    """Does `rect` lie completely inside the picture `size` after it was turned clockwise by `angle`?"""
    convert, _c, _s = _to_source(angle, size)
    x, y, w, h = rect
    for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
        sx, sy = convert(px, py)
        if sx < -eps or sy < -eps or sx > size[0] + eps or sy > size[1] + eps:
            return False
    return True


def _max_scale(rect: Rect, size: Size, angle: float) -> float:
    """The largest factor (<= 1) by which `rect` can be scaled about its centre and still be valid."""
    convert, c, s = _to_source(angle, size)
    x, y, w, h = rect
    bx, by = convert(x + w / 2, y + h / 2)
    best = 1.0
    for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        dx, dy = ox * w / 2, oy * h / 2
        for base, delta, limit in ((bx, c * dx + s * dy, size[0]), (by, -s * dx + c * dy, size[1])):
            if delta > 1e-9:
                best = min(best, (limit - base) / delta)
            elif delta < -1e-9:
                best = min(best, -base / delta)
    return max(0.0, best)


def fit_rect(rect: Rect, size: Size, angle: float) -> Rect:
    """`rect` shrunk about its centre until it is valid; a rect whose centre fell outside is re-centred."""
    scale = _max_scale(rect, size, angle)
    x, y, w, h = rect
    if scale <= 0:
        x, y = (size[0] - w) / 2, (size[1] - h) / 2
        scale = _max_scale((x, y, w, h), size, angle)
    cx, cy = x + w / 2, y + h / 2
    w, h = w * scale, h * scale
    return cx - w / 2, cy - h / 2, w, h


def largest_rect(size: Size, angle: float, aspect: float | None) -> Rect:
    """The biggest centred rect of the given shape (w / h; None = the picture's own shape) that is valid."""
    w, h = size
    aspect = aspect or w / max(h, 1)
    rw = min(w, h * aspect)
    rect = ((w - rw) / 2, (h - rw / aspect) / 2, rw, rw / aspect)
    return fit_rect(rect, size, angle)


def approach(old: Rect, new: Rect, size: Size, angle: float) -> Rect:
    """Move from the valid rect `old` towards `new`, stopping where the picture's edge is reached.

    The valid rectangles form a convex set, so a bisection along the line between the two is exact.
    """
    if rect_valid(new, size, angle):
        return new
    lo, hi = 0.0, 1.0
    for _ in range(18):
        mid = (lo + hi) / 2
        probe = tuple(a + (b - a) * mid for a, b in zip(old, new))
        if rect_valid(probe, size, angle):  # type: ignore[arg-type]
            lo = mid
        else:
            hi = mid
    return tuple(a + (b - a) * lo for a, b in zip(old, new))  # type: ignore[return-value]


# // pixels


def render(image: Image.Image, state: EditState) -> Image.Image:
    """`image` (already upright) with the edit applied."""
    return apply_adjustments(apply_filter(_geometry(image, state), state.filter), state.adjust)


def render_preview(base: Image.Image, state: EditState, full: Size, max_side: int) -> Image.Image:
    """The turns, straighten and crop of `state` on the preview `base` of a photo of size `full`, shrunk to
    at most `max_side` pixels, without the filter and adjustments (the Adjustment and Filter tools apply those themselves)."""
    work_full = working_size(full, state.ops)
    work_preview = apply_ops(base, state.ops).size
    factor = work_preview[0] / max(work_full[0], 1.0)  # the crop is in pixels of the real photo
    crop = state.crop
    if crop is not None:
        crop = tuple(value * factor for value in crop)
    result = _geometry(base, replace(state, crop=crop))
    if max(result.size) > max_side:
        shrink = max_side / max(result.size)
        result = result.resize(
            (max(1, round(result.width * shrink)), max(1, round(result.height * shrink))), Image.Resampling.LANCZOS
        )
    return result


def _geometry(image: Image.Image, state: EditState) -> Image.Image:
    """`image` turned, flipped, straightened and cropped; the filter and adjustments are not applied."""
    work = apply_ops(image, state.ops)
    width, height = work.size
    x, y, w, h = state.crop or (0.0, 0.0, float(width), float(height))
    out_w, out_h = max(1, round(w)), max(1, round(h))
    if abs(state.angle) < 1e-6:
        left = max(0, min(width - out_w, round(x)))
        top = max(0, min(height - out_h, round(y)))
        if (left, top, out_w, out_h) == (0, 0, width, height):
            return work
        return work.crop((left, top, left + out_w, top + out_h))
    if work.mode in ("P", "1"):
        work = work.convert("RGBA" if "transparency" in work.info else "RGB")
    t = math.radians(state.angle)
    c, s = math.cos(t), math.sin(t)
    cx, cy = width / 2, height / 2
    coeffs = (
        c, s, cx + c * (x - cx) + s * (y - cy),
        -s, c, cy - s * (x - cx) + c * (y - cy),
    )
    return work.transform((out_w, out_h), Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BICUBIC)


def load_for_edit(path: Path, max_side: int = 2400) -> tuple[Image.Image, tuple[int, int]]:
    """(preview, full size) of the upright photo at `path` (a plain image or a .npi). Raises RotateError."""
    try:
        data = read_payload(path, read_record(path)) if is_npi_path(path) else path.read_bytes()
        with Image.open(io.BytesIO(data)) as source:
            if getattr(source, "n_frames", 1) > 1:
                raise RotateError("Animated or multi-page images can't be edited.")
            source.load()
            upright = ImageOps.exif_transpose(source)
        full = upright.size
        preview = upright.convert("RGBA" if "A" in upright.getbands() else "RGB")
        preview.thumbnail((max_side, max_side))
        return preview, full
    except RotateError:
        raise
    except Exception as error:
        raise RotateError(f"The photo can't be opened for editing: {error}") from error


def edit_image_bytes(data: bytes, state: EditState) -> bytes:
    """`data` (a whole image file) with the edit applied, in the same format and with its metadata."""
    image_format = detect_format(data)
    if image_format not in SUPPORTED:
        name = "This kind of" if image_format is ImageFormat.UNKNOWN else image_format.name
        raise RotateError(f"{name} photos can't be edited permanently.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if getattr(source, "n_frames", 1) > 1:
                raise RotateError("Animated or multi-page images can't be edited.")
            source.load()
            exif = source.getexif()
            icc = source.info.get("icc_profile")
            dpi = source.info.get("dpi")
            xmp = source.info.get("xmp")
            transparency = source.info.get("transparency")
            compression = source.info.get("compression")
            subsampling = JpegImagePlugin.get_sampling(source) if image_format is ImageFormat.JPEG else -1
            result = render(ImageOps.exif_transpose(source), state)
            result.load()
    except RotateError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise RotateError(f"The photo can't be read: {error}") from error

    extras: dict = {}
    exif_bytes = _fix_exif(exif, *result.size, is_tiff=image_format is ImageFormat.TIFF)
    if exif_bytes:
        extras["exif"] = exif_bytes
    if icc:
        extras["icc_profile"] = icc
    if dpi:
        extras["dpi"] = dpi
    options: dict = {}
    if image_format is ImageFormat.JPEG:
        options = {"quality": JPEG_QUALITY, "optimize": True}
        if subsampling in (0, 1, 2):
            options["subsampling"] = subsampling
        if isinstance(xmp, bytes) and xmp:
            extras["xmp"] = xmp
        if result.mode not in ("RGB", "L", "CMYK"):
            result = result.convert("RGB")
    elif image_format is ImageFormat.PNG:
        options = {"compress_level": 6}
    elif image_format is ImageFormat.WEBP:
        lossless = data[12:16] == b"VP8L"
        options = {"lossless": True, "quality": 100} if lossless else {"quality": JPEG_QUALITY}
    elif image_format is ImageFormat.GIF:
        if transparency is not None:
            options["transparency"] = transparency
        extras.pop("exif", None)
        extras.pop("dpi", None)
    elif image_format is ImageFormat.TIFF and isinstance(compression, str):
        options["compression"] = compression
    elif image_format is ImageFormat.BMP:
        extras.pop("exif", None)
        extras.pop("icc_profile", None)
        if result.mode == "RGBA":
            result = result.convert("RGB")

    for attempt in (dict(options, **extras), options):  # second try: without the metadata extras
        buffer = io.BytesIO()
        try:
            result.save(buffer, image_format.name, **attempt)
            return buffer.getvalue()
        except (OSError, ValueError, TypeError, KeyError):
            continue
    raise RotateError(f"The edited {image_format.name} photo could not be saved.")


def save_edit(path: Path, state: EditState, copy: bool) -> Path:
    """Write the edit: as a new file next to `path` (copy=True) or over it. Returns the file written."""
    try:
        if is_npi_path(path):
            record = read_record(path)
            data = edit_image_bytes(read_payload(path, record), state)
            info = inspect_image(data)
            meta = replace(
                record.meta,
                size=len(data),
                width=info.width,
                height=info.height,
                sha256=hashlib.sha256(data).hexdigest(),
                taken_at=info.taken_at if info.taken_at is not None else record.meta.taken_at,
            )
            target = path
            if copy:
                target = unique_path(path.with_name(f"{path.stem}{EDITED_SUFFIX}{path.suffix}"))
                original = Path(meta.original_name)
                meta = replace(meta, original_name=f"{original.stem}{EDITED_SUFFIX}{original.suffix}")
            thumbnail = build_thumbnail(io.BytesIO(data))

            def write() -> None:
                write_npi(target, meta, info.exif, info.icc, thumbnail, data)

        else:
            data = edit_image_bytes(path.read_bytes(), state)
            target = unique_path(path.with_name(f"{path.stem}{EDITED_SUFFIX}{path.suffix}")) if copy else path

            def write() -> None:
                write_atomic(target, data)

        if copy:
            write()
        else:
            _rewrite(path, write)
        return target
    except RotateError:
        raise
    except OSError as error:
        raise RotateError(f"The file could not be written: {error}") from error
    except Exception as error:
        raise RotateError(str(error)) from error