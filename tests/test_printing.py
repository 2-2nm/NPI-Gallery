import pytest

from npigallery.services.printing import (
    PrintOptions,
    effective_dpi,
    is_landscape,
    place,
    quality_label,
    sheet_size,
)

A4 = (210.0, 297.0)


def test_orientation_follows_the_photo_unless_chosen():
    assert is_landscape("auto", 1920, 1080)
    assert not is_landscape("auto", 1080, 1920)
    assert not is_landscape("auto", 1000, 1000)
    assert is_landscape("landscape", 1080, 1920)
    assert not is_landscape("portrait", 1920, 1080)


def test_sheet_size_swaps_sides_for_landscape():
    assert sheet_size(A4, False) == (210.0, 297.0)
    assert sheet_size(A4, True) == (297.0, 210.0)
    assert sheet_size((297.0, 210.0), False) == (210.0, 297.0)  # the paper may be given either way round


def test_fit_keeps_the_whole_photo_centred_inside_the_margins():
    placement = place(sheet_size(A4, True), (1920, 1080), PrintOptions(margin="none"))
    x, y, w, h = placement.frame
    assert placement.source == (0.0, 0.0, 1920.0, 1080.0)
    assert w == pytest.approx(297.0) and h == pytest.approx(297.0 * 1080 / 1920)
    assert x == pytest.approx(0.0) and y == pytest.approx((210.0 - h) / 2)


def test_margins_shrink_the_frame():
    plain = place(sheet_size(A4, False), (1000, 1500), PrintOptions(margin="none")).frame
    padded = place(sheet_size(A4, False), (1000, 1500), PrintOptions(margin="normal")).frame
    assert padded[2] < plain[2]
    assert padded[0] >= 12.7 - 1e-9


def test_fill_crops_the_photo_to_the_frame():
    # a wide photo on an upright page: the sides are cut, the frame is the whole printable area
    placement = place(sheet_size(A4, False), (3000, 2000), PrintOptions(margin="none", fill=True))
    assert placement.frame == (0.0, 0.0, 210.0, 297.0)
    sx, sy, sw, sh = placement.source
    assert sy == 0.0 and sh == 2000.0
    assert sw / sh == pytest.approx(210.0 / 297.0)
    assert sx == pytest.approx((3000 - sw) / 2)


def test_fixed_size_frame_is_centred_and_oriented_like_the_page():
    placement = place(sheet_size(A4, False), (1500, 1000), PrintOptions(size="10x15", margin="none"))
    x, y, w, h = placement.frame
    assert w <= 100.0 + 1e-9 and h <= 150.0 + 1e-9
    assert x + w / 2 == pytest.approx(105.0) and y + h / 2 == pytest.approx(148.5)


def test_fixed_size_larger_than_the_sheet_is_made_to_fit():
    a5 = sheet_size((148.0, 210.0), False)
    x, y, w, h = place(a5, (1000, 1000), PrintOptions(size="20x25", margin="small")).frame
    assert x >= 6.0 - 1e-9 and y >= 6.0 - 1e-9
    assert x + w <= 148.0 - 6.0 + 1e-9 and y + h <= 210.0 - 6.0 + 1e-9


def test_effective_dpi_and_label():
    placement = place(sheet_size((101.6, 152.4), False), (1200, 1800), PrintOptions(margin="none"))  # 4 x 6 in
    assert effective_dpi(placement) == pytest.approx(300.0)
    assert quality_label(300) == "sharp"
    assert quality_label(150).startswith("fine")
    assert quality_label(72).startswith("low")
