"""The Adjustment tool's pixels: ten sliders (Light and Color) and the code that applies them.

Every slider is a whole number from -100 to 100 and 0 means "leave it alone". `Adjustments` is cheap to
copy, so it lives inside `editing.EditState` and the editor's undo history covers it for free.

Only Pillow is used. Brightness, exposure, contrast, highlights and shadows are folded into one tone curve
and warmth / tint into per-channel gains, so those are a single table lookup per pixel.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from functools import lru_cache

from PIL import Image, ImageChops, ImageEnhance, ImageFilter

AMOUNT = 100  # a slider runs from -AMOUNT to +AMOUNT

LIGHT = ("brightness", "exposure", "contrast", "highlights", "shadows", "vignette")
COLOR = ("saturation", "warmth", "tint", "sharpness")


@dataclass(frozen=True)
class Adjustments:
    brightness: int = 0
    exposure: int = 0
    contrast: int = 0
    highlights: int = 0
    shadows: int = 0
    vignette: int = 0  # > 0 darkens the corners, < 0 lightens them
    saturation: int = 0
    warmth: int = 0  # > 0 warmer (orange), < 0 cooler (blue)
    tint: int = 0  # > 0 towards magenta, < 0 towards green
    sharpness: int = 0  # > 0 sharper, < 0 softer

    def is_identity(self) -> bool:
        return all(getattr(self, field.name) == 0 for field in fields(self))


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _tone_curve(adj: Adjustments) -> list[float]:
    """The 256-entry curve (0..1 in, 0..1 out) for brightness, exposure, contrast, highlights and shadows."""
    brightness, exposure, contrast = adj.brightness / AMOUNT, adj.exposure / AMOUNT, adj.contrast / AMOUNT
    highlights, shadows = adj.highlights / AMOUNT, adj.shadows / AMOUNT
    curve = []
    for i in range(256):
        x = i / 255
        if exposure:  # in linear light, so +1 is two stops brighter
            x = _clamp((x**2.2) * 2 ** (exposure * 2)) ** (1 / 2.2)
        if brightness < 0:  # -100 takes white down to about 23 % (measured against the Windows Photos tool)
            x *= 1 + 0.77 * brightness
        elif brightness > 0:  # and +100 lifts black up by the same amount
            x = 1 - (1 - x) * (1 - 0.77 * brightness)
        if contrast:
            x = _clamp(0.5 + (x - 0.5) * 2**contrast)
        if highlights:
            x = _clamp(x + 0.35 * highlights * x**3)
        if shadows:
            x = _clamp(x + 0.35 * shadows * (1 - x) ** 3)
        curve.append(x)
    return curve


def _channel_gains(adj: Adjustments) -> tuple[float, float, float]:
    warmth, tint = adj.warmth / AMOUNT, adj.tint / AMOUNT
    return 1 + 0.25 * warmth + 0.05 * tint, 1 - 0.20 * tint, 1 - 0.25 * warmth + 0.05 * tint


def _lut(adj: Adjustments) -> list[int]:
    curve = _tone_curve(adj)
    table: list[int] = []
    for gain in _channel_gains(adj):
        table.extend(min(255, max(0, round(value * 255 * gain))) for value in curve)
    return table


@lru_cache(maxsize=2)
def _falloff(size: tuple[int, int]) -> Image.Image:
    """0 in the middle, rising to 255 at the edges (an ellipse as wide and high as the picture)."""
    ramp = Image.radial_gradient("L").point([min(255, max(0, round((i - 90) * 255 / 165))) for i in range(256)])
    return ramp.resize(size, Image.Resampling.BICUBIC)


def _vignette(rgb: Image.Image, amount: int) -> Image.Image:
    edge = _falloff(rgb.size).point([round((i / 255) ** 1.6 * 255) for i in range(256)])
    strength = abs(amount) / AMOUNT
    if amount > 0:
        gray = edge.point([round(255 * (1 - 0.85 * strength * i / 255)) for i in range(256)])
        return ImageChops.multiply(rgb, Image.merge("RGB", (gray, gray, gray)))
    mask = edge.point([round(255 * 0.8 * strength * i / 255) for i in range(256)])
    return Image.composite(Image.new("RGB", rgb.size, (255, 255, 255)), rgb, mask)


def _sharpen(rgb: Image.Image, amount: int) -> Image.Image:
    strength = amount / AMOUNT
    radius = min(6.0, max(0.6, max(rgb.size) / 2000 * 1.2))  # the same look at any size of the picture
    if strength > 0:
        return rgb.filter(ImageFilter.UnsharpMask(radius=radius, percent=round(strength * 250), threshold=2))
    return Image.blend(rgb, rgb.filter(ImageFilter.GaussianBlur(radius)), -strength * 0.8)


def apply_adjustments(image: Image.Image, adj: Adjustments, vignette: bool = True) -> Image.Image:
    """`image` with the sliders applied (a new image; `image` itself when there is nothing to do).

    `vignette=False` leaves the vignette out: while the crop frame is being moved the picture is shown
    whole, and the vignette belongs to the cropped result.
    """
    amount = adj.vignette if vignette else 0
    tone = any((adj.brightness, adj.exposure, adj.contrast, adj.highlights, adj.shadows, adj.warmth, adj.tint))
    if not (tone or amount or adj.saturation or adj.sharpness):
        return image
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    if tone:
        rgb = rgb.point(_lut(adj))
    if adj.saturation:
        rgb = ImageEnhance.Color(rgb).enhance(1 + adj.saturation / AMOUNT)
    if adj.sharpness:
        rgb = _sharpen(rgb, adj.sharpness)
    if amount:
        rgb = _vignette(rgb, amount)
    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb