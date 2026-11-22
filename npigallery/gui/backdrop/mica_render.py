"""The blurred-wallpaper picture behind the window in Mica mode (Pillow only, no Qt).

Windows paints Mica from the desktop wallpaper, laid out the way the desktop shows it and then blurred
heavily. This module does the same on a small working canvas: the wallpaper is fitted to the screen with
the desktop's own fit style (fill, fit, stretch, center, tile, span), blurred, and returned as an RGB
picture the size of the working canvas. The caller scales it up to the screen and lays the tint over it.
"""

from __future__ import annotations

from PIL import Image, ImageEnhance, ImageFilter

WORK_WIDTH = 640  # the working canvas is this wide: a blur this heavy does not need more pixels
MAX_MICA_BLUR = 24  # the largest value of the "Blur" setting
BLUR_STEP = 2.0  # blur radius, in working pixels, per step of the setting

FILL, FIT, STRETCH, CENTER, TILE, SPAN = "fill", "fit", "stretch", "center", "tile", "span"

ACRYLIC_SATURATION = 1.25  # Windows' acrylic adds a little colour to what it blurs
NOISE_SIZE = 128  # side of the repeating grain tile


def style_from_registry(wallpaper_style: str, tile_wallpaper: str) -> str:
    """The desktop's fit style from the WallpaperStyle / TileWallpaper registry values."""
    if str(tile_wallpaper).strip() == "1":
        return TILE
    return {"10": FILL, "6": FIT, "2": STRETCH, "0": CENTER, "22": SPAN}.get(str(wallpaper_style).strip(), FILL)


def blur_radius(blur: int, work_width: int = WORK_WIDTH) -> float:
    """Gaussian radius, in working pixels, for the setting `blur` (0-MAX_MICA_BLUR)."""
    blur = max(0, min(MAX_MICA_BLUR, int(blur)))
    return blur * BLUR_STEP * work_width / WORK_WIDTH


def _scaled(picture: Image.Image, size: tuple[int, int]) -> Image.Image:
    size = (max(1, size[0]), max(1, size[1]))
    if picture.size == size:
        return picture
    return picture.resize(size, Image.Resampling.BILINEAR, reducing_gap=2.0)


def lay_out(
    picture: Image.Image | None, style: str, canvas: tuple[int, int], fill: tuple[int, int, int], native_scale: float
) -> Image.Image:
    """`picture` placed on a canvas of `canvas` pixels the way the desktop does for `style`.

    `native_scale` is how much smaller the canvas is than the real screen, so that center / tile keep
    the picture at the size it has on the real desktop. `picture` None gives the plain fill colour.
    """
    width, height = max(1, canvas[0]), max(1, canvas[1])
    result = Image.new("RGB", (width, height), fill)
    if picture is None:
        return result
    pic_w, pic_h = picture.size
    if style in (FILL, SPAN):
        scale = max(width / pic_w, height / pic_h)
        covered = _scaled(picture, (round(pic_w * scale), round(pic_h * scale)))
        result.paste(covered, ((width - covered.width) // 2, (height - covered.height) // 2))
    elif style == FIT:
        scale = min(width / pic_w, height / pic_h)
        fitted = _scaled(picture, (round(pic_w * scale), round(pic_h * scale)))
        result.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    elif style == STRETCH:
        result = _scaled(picture, (width, height))
    elif style == CENTER:
        native = _scaled(picture, (round(pic_w * native_scale), round(pic_h * native_scale)))
        result.paste(native, ((width - native.width) // 2, (height - native.height) // 2))
    else:  # TILE
        tile = _scaled(picture, (round(pic_w * native_scale), round(pic_h * native_scale)))
        for top in range(0, height, tile.height):
            for left in range(0, width, tile.width):
                result.paste(tile, (left, top))
    return result.convert("RGB")


def render(
    path: str | None,
    style: str,
    canvas_size: tuple[int, int],
    region: tuple[float, float, float, float],
    fill: tuple[int, int, int],
    blur: int,
    saturation: float = 1.0,
) -> Image.Image:
    """The blurred wallpaper for one screen, its colours scaled by `saturation` (1 keeps them).

    `canvas_size` is the area the wallpaper is laid out on, in screen pixels: the screen itself, or the
    whole virtual desktop for the Span style. `region` is the part of that canvas this screen shows, as
    fractions (left, top, width, height) of the canvas. Returns an RGB picture about WORK_WIDTH wide.
    """
    canvas_w, canvas_h = max(1, canvas_size[0]), max(1, canvas_size[1])
    scale = WORK_WIDTH / canvas_w
    work = (WORK_WIDTH, max(1, round(canvas_h * scale)))
    picture = None
    if path:
        try:
            with Image.open(path) as opened:
                opened.draft("RGB", (work[0] * 2, work[1] * 2))  # JPEGs decode at a fraction of their size
                picture = opened.convert("RGB")
        except (OSError, ValueError):
            picture = None
    canvas = lay_out(picture, style, work, fill, scale)
    left, top, width, height = region
    box = (
        round(left * canvas.width),
        round(top * canvas.height),
        max(round((left + width) * canvas.width), round(left * canvas.width) + 1),
        max(round((top + height) * canvas.height), round(top * canvas.height) + 1),
    )
    cropped = canvas.crop(box)
    radius = blur_radius(blur)
    if radius > 0:
        cropped = cropped.filter(ImageFilter.GaussianBlur(radius))
    if abs(saturation - 1.0) > 0.001:
        cropped = ImageEnhance.Color(cropped).enhance(saturation)
    return cropped


def noise_tile(size: int = NOISE_SIZE) -> Image.Image:
    """A square tile of fine grey grain, the texture Windows lays over its acrylic. Mode "L", mean mid-grey."""
    return Image.effect_noise((size, size), 48)


Rect = tuple[float, float, float, float]  # x, y, width, height


def clip_source(source: Rect, target: Rect, bounds: Rect) -> tuple[Rect, Rect] | None:
    """Cut the part of `source` that lies outside `bounds` away, and the matching part off `target`.

    Drawing a piece of a picture where the piece sticks out of the picture would leave the outside part
    unpainted (or stretch the rest); the caller draws the returned pair instead. None: nothing is left.
    """
    sx, sy, sw, sh = source
    bx, by, bw, bh = bounds
    left, top = max(sx, bx), max(sy, by)
    right, bottom = min(sx + sw, bx + bw), min(sy + sh, by + bh)
    if sw <= 0 or sh <= 0 or right <= left or bottom <= top:
        return None
    tx, ty, tw, th = target
    scale_x, scale_y = tw / sw, th / sh
    clipped_target = (tx + (left - sx) * scale_x, ty + (top - sy) * scale_y, (right - left) * scale_x, (bottom - top) * scale_y)
    return (left, top, right - left, bottom - top), clipped_target