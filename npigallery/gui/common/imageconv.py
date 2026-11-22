import io

from PIL import Image
from PySide6.QtGui import QImage

from ...npi.imaging import pixel_buffer


def pil_to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return qimage.copy()


def qimage_from_display(image: Image.Image) -> QImage:
    """QImage of an RGB / RGBA Pillow image (see `load_oriented_display`), keeping memory low.

    This *consumes* `image`: its pixels are freed as soon as they are copied out, so at most two
    full-size buffers exist at any moment (the old chain had up to five), and an opaque photo is
    stored with 3 bytes per pixel instead of 4.
    """
    if image.mode == "RGBA":
        layout, bytes_per_pixel = QImage.Format.Format_RGBA8888, 4
    else:
        image = image.convert("RGB") if image.mode != "RGB" else image
        layout, bytes_per_pixel = QImage.Format.Format_RGB888, 3
    width, height = image.size
    raw = pixel_buffer(image)
    image.close()
    # The wrapper only borrows `raw`; copy() gives the QImage its own (aligned) pixels.
    try:
        wrapper = QImage(raw, width, height, width * bytes_per_pixel, layout)
    except TypeError:  # a binding that only takes `bytes`
        wrapper = QImage(bytes(raw), width, height, width * bytes_per_pixel, layout)
    return wrapper.copy()


def qimage_from_bytes(data: bytes) -> QImage:
    image = QImage.fromData(data)
    if not image.isNull():
        return image
    with Image.open(io.BytesIO(data)) as pil_image:
        return pil_to_qimage(pil_image)