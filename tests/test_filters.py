from PIL import Image

from npigallery.services import editing as ed
from npigallery.services.filters import FILTERS, ORIGINAL, FilterChoice, apply_filter


def _photo() -> Image.Image:
    image = Image.new("RGB", (32, 24))
    image.putdata([(x * 8, y * 10, (x + y) * 4) for y in range(24) for x in range(32)])
    return image


def test_original_and_zero_intensity_return_the_photo_itself():
    photo = _photo()
    assert apply_filter(photo, FilterChoice()) is photo
    assert apply_filter(photo, FilterChoice("punch", 0)) is photo


def test_every_look_keeps_size_and_changes_pixels():
    photo = _photo()
    for key, _label in FILTERS:
        result = apply_filter(photo, FilterChoice(key))
        assert result.size == photo.size
        if key != ORIGINAL:
            assert result.tobytes() != photo.tobytes()


def test_black_and_white_looks_have_little_colour_spread():
    photo = _photo()
    red, green, blue = apply_filter(photo, FilterChoice("bw")).split()
    assert max(abs(a - b) for a, b in zip(red.getdata(), blue.getdata())) <= 12


def test_intensity_blends_between_photo_and_full_look():
    photo = _photo()
    full = apply_filter(photo, FilterChoice("film", 100))
    half = apply_filter(photo, FilterChoice("film", 50))
    assert half.tobytes() != full.tobytes() and half.tobytes() != photo.tobytes()
    pixel = lambda image: image.getpixel((20, 12))
    for a, b, c in zip(pixel(photo), pixel(half), pixel(full)):
        assert min(a, c) - 1 <= b <= max(a, c) + 1


def test_alpha_is_kept():
    photo = _photo().convert("RGBA")
    photo.putalpha(Image.new("L", photo.size, 90))
    assert apply_filter(photo, FilterChoice("vintage")).getchannel("A").getextrema() == (90, 90)


def test_filter_takes_part_in_the_edit_state():
    state = ed.EditState()
    assert state.is_identity((32, 24))
    looked = ed.EditState(filter=FilterChoice("calm", 70))
    assert not looked.is_identity((32, 24))
    photo = _photo()
    assert ed.render(photo, looked).tobytes() == apply_filter(photo, FilterChoice("calm", 70)).tobytes()
    assert ed.render(photo, state).tobytes() == photo.tobytes()
