"""Tests for the overlay renderer."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from drainwatch.choke.overlay import colorize_mask, render_overlay
from drainwatch.config import CLASS_COLORS, Class


def gray_image(size=(64, 48), value=128) -> Image.Image:
    """A flat grey image. size is PIL's (width, height)."""
    return Image.new("RGB", size, (value, value, value))


def test_colorize_maps_each_class_to_its_colour():
    mask = np.array([[Class.STRUCTURE, Class.WATER], [Class.WASTE, Class.WASTE]], dtype=np.uint8)

    rgb = colorize_mask(mask)

    assert tuple(rgb[0, 0]) == CLASS_COLORS[Class.STRUCTURE]
    assert tuple(rgb[0, 1]) == CLASS_COLORS[Class.WATER]
    assert tuple(rgb[1, 0]) == CLASS_COLORS[Class.WASTE]


def test_structure_pixels_are_left_untinted():
    """Tinting the whole frame would hide the drain and make the overlay unreadable."""
    mask = np.full((48, 64), int(Class.STRUCTURE), dtype=np.uint8)

    out = np.asarray(render_overlay(gray_image(), mask))

    assert (out == 128).all()


def test_waste_pixels_are_tinted_toward_magenta():
    mask = np.full((48, 64), int(Class.STRUCTURE), dtype=np.uint8)
    mask[:24, :] = Class.WASTE

    out = np.asarray(render_overlay(gray_image(), mask, alpha=0.5))

    r, g, b = out[10, 10]
    # (255, 0, 110) over grey 128 at alpha 0.5 → (192, 64, 119). Blue sits just *below*
    # grey, so the distinguishing property is the ordering, not "every channel rises":
    # red clearly highest, green clearly lowest. That is what separates the waste tint
    # from the water tint (0, 150, 255), where blue dominates instead.
    assert r > 128 > g
    assert r > b > g
    assert (out[40, 10] == 128).all()  # structure half untouched


def test_alpha_controls_blend_strength():
    mask = np.full((48, 64), int(Class.WASTE), dtype=np.uint8)

    faint = np.asarray(render_overlay(gray_image(), mask, alpha=0.1))
    strong = np.asarray(render_overlay(gray_image(), mask, alpha=0.9))

    # Waste red channel is 255, so stronger alpha pushes further from grey 128.
    assert strong[0, 0][0] > faint[0, 0][0] > 128


def test_image_is_resized_to_mask_resolution():
    """Inference runs at 512px; the source photo is whatever the camera produced."""
    mask = np.full((48, 64), int(Class.WATER), dtype=np.uint8)

    out = render_overlay(gray_image(size=(320, 240)), mask)

    assert out.size == (64, 48)  # PIL (width, height) matching mask (48, 64)


def test_output_is_rgb_even_for_grayscale_input():
    mask = np.full((16, 16), int(Class.WATER), dtype=np.uint8)

    out = render_overlay(Image.new("L", (16, 16), 90), mask)

    assert out.mode == "RGB"


@pytest.mark.parametrize("alpha", [-0.1, 1.5])
def test_rejects_out_of_range_alpha(alpha):
    mask = np.zeros((16, 16), dtype=np.uint8)

    with pytest.raises(ValueError, match="alpha"):
        render_overlay(gray_image(size=(16, 16)), mask, alpha=alpha)


def test_rejects_non_2d_mask():
    with pytest.raises(ValueError, match="2-D"):
        render_overlay(gray_image(), np.zeros((16, 16, 3), dtype=np.uint8))
