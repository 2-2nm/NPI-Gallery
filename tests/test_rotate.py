import io
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from npigallery.npi import pack_file, read_image_bytes, read_meta, verify_npi
from npigallery.services.rotate import RotateError, rotate_file, rotate_image_bytes

RED, BLUE = (230, 20, 20), (20, 20, 230)


def sample(width=64, height=32) -> Image.Image:
    """Left half red, right half blue: easy to see which way it was turned."""
    image = Image.new("RGB", (width, height), RED)
    image.paste(BLUE, (width // 2, 0, width, height))
    return image


def encode(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def near(pixel, expected, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], expected))


class RotateBytesTests(unittest.TestCase):
    def check_clockwise(self, fmt: str, **options):
        out = Image.open(io.BytesIO(rotate_image_bytes(encode(sample(), fmt, **options), 90)))
        self.assertEqual(out.format, fmt)
        self.assertEqual(out.size, (32, 64))
        rgb = out.convert("RGB")
        # clockwise: the left (red) half ends up on top, the right (blue) half at the bottom
        self.assertTrue(near(rgb.getpixel((16, 8)), RED))
        self.assertTrue(near(rgb.getpixel((16, 56)), BLUE))

    def test_clockwise_for_every_supported_format(self):
        for fmt, options in (("JPEG", {"quality": 95}), ("PNG", {}), ("WEBP", {"lossless": True}), ("BMP", {}), ("TIFF", {})):
            with self.subTest(fmt=fmt):
                self.check_clockwise(fmt, **options)

    def test_180_and_270(self):
        data = encode(sample(), "PNG")
        half_turn = Image.open(io.BytesIO(rotate_image_bytes(data, 180))).convert("RGB")
        self.assertEqual(half_turn.size, (64, 32))
        self.assertTrue(near(half_turn.getpixel((8, 16)), BLUE))
        quarter = Image.open(io.BytesIO(rotate_image_bytes(data, 270))).convert("RGB")
        self.assertTrue(near(quarter.getpixel((16, 8)), BLUE))  # counter-clockwise: blue on top
        self.assertTrue(near(quarter.getpixel((16, 56)), RED))

    def test_minus_90_equals_270_and_four_turns_come_back(self):
        data = encode(sample(), "PNG")
        self.assertEqual(rotate_image_bytes(data, -90), rotate_image_bytes(data, 270))
        self.assertIs(rotate_image_bytes(data, 360), data)

    def test_exif_orientation_is_baked_in_not_applied_twice(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 cw to display": the file stores sideways pixels
        data = encode(sample(), "JPEG", exif=exif.tobytes(), quality=95)
        out = Image.open(io.BytesIO(rotate_image_bytes(data, 90)))
        self.assertEqual(out.size, (64, 32))  # sideways + upright turn + our turn = 180 total
        self.assertNotIn(0x0112, out.getexif())

    def test_other_exif_and_icc_survive(self):
        exif = Image.Exif()
        exif[0x010F] = "NPI Camera"
        exif[0x0112] = 1
        icc = Image.open(io.BytesIO(encode(sample(), "PNG"))).info.get("icc_profile")
        data = encode(sample(), "JPEG", exif=exif.tobytes(), quality=95)
        out = Image.open(io.BytesIO(rotate_image_bytes(data, 90)))
        self.assertEqual(out.getexif().get(0x010F), "NPI Camera")
        self.assertIsNone(icc)

    def test_refuses_what_it_cannot_do(self):
        frames = [sample().convert("P"), sample().transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("P"), Image.new("P", (64, 32), 3)]
        gif = io.BytesIO()
        frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:])
        with self.assertRaises(RotateError):
            rotate_image_bytes(gif.getvalue(), 90)  # animated
        with self.assertRaises(RotateError):
            rotate_image_bytes(b"not an image at all", 90)
        with self.assertRaises(RotateError):
            rotate_image_bytes(encode(sample(), "PNG"), 45)

    def test_single_frame_gif_keeps_working(self):
        data = encode(sample().convert("P"), "GIF")
        self.assertEqual(Image.open(io.BytesIO(rotate_image_bytes(data, 90))).size, (32, 64))


class RotateFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_file_is_rewritten_and_keeps_its_timestamp(self):
        path = self.dir / "a.jpg"
        sample().save(path, "JPEG", quality=95)
        old = (1_700_000_000_000_000_000, 1_700_000_000_000_000_000)
        os.utime(path, ns=old)
        size = path.stat().st_size
        rotate_file(path, 90)
        self.assertEqual(Image.open(path).size, (32, 64))
        stat = path.stat()
        self.assertAlmostEqual(stat.st_mtime_ns, old[1], delta=10_000)
        self.assertTrue((stat.st_mtime_ns, stat.st_size) != (old[1], size))  # cache key changed
        self.assertFalse(list(self.dir.glob("*.tmp")))

    def test_same_size_still_changes_the_cache_key(self):
        path = self.dir / "square.bmp"
        sample(32, 32).save(path, "BMP")
        old = 1_700_000_000_000_000_000
        os.utime(path, ns=(old, old))
        size = path.stat().st_size
        rotate_file(path, 90)
        self.assertEqual(path.stat().st_size, size)
        self.assertNotEqual(path.stat().st_mtime_ns, old)

    def test_npi_container_is_rebuilt_consistently(self):
        source = self.dir / "b.png"
        sample().save(source, "PNG")
        npi = self.dir / "b.npi"
        pack_file(source, npi)
        before = read_meta(npi)
        rotate_file(npi, 90)
        after = verify_npi(npi)  # checksum matches the new payload
        self.assertEqual((before.width, before.height), (64, 32))
        self.assertEqual((after.width, after.height), (32, 64))
        self.assertEqual(after.original_name, "b.png")
        self.assertEqual(after.mtime_ns, before.mtime_ns)
        self.assertEqual(Image.open(io.BytesIO(read_image_bytes(npi))).size, (32, 64))

    def test_failure_leaves_the_file_untouched(self):
        path = self.dir / "c.png"
        path.write_bytes(b"garbage")
        with self.assertRaises(RotateError):
            rotate_file(path, 90)
        self.assertEqual(path.read_bytes(), b"garbage")

    def test_heic_style_files_are_refused_cleanly(self):
        path = self.dir / "d.heic"
        path.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32)
        with self.assertRaises(RotateError) as caught:
            rotate_file(path, 90)
        self.assertIn("HEIC", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
