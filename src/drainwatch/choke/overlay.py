"""Render a segmentation mask as a colour overlay.

This is what a non-technical stakeholder actually looks at, and what the 3-minute demo
video shows, so legibility beats subtlety: waste is magenta, water is bright blue, and
structure is left untinted so the original drain stays recognisable.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from drainwatch.config import CLASS_COLORS, Class


def colorize_mask(mask: np.ndarray) -> np.ndarray:
    """Map a (H, W) label array to an (H, W, 3) uint8 RGB array."""
    rgb = np.zeros((*mask.shape, 3), dtype=np.uint8)
    for cls, color in CLASS_COLORS.items():
        rgb[mask == cls] = color
    return rgb


def render_overlay(
    image: Image.Image,
    mask: np.ndarray,
    *,
    alpha: float = 0.55,
) -> Image.Image:
    """Blend a class mask over the source image.

    ``STRUCTURE`` pixels are left untouched — tinting the whole frame would hide the
    drain itself and make the result unreadable.

    Args:
        image: source photo; resized to the mask resolution if they differ.
        mask: (H, W) integer label array.
        alpha: opacity of the tint over water/waste pixels, in [0, 1].

    Returns:
        An RGB image the same size as ``mask``.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")
    if mask.ndim != 2:
        raise ValueError(f"mask must be 2-D (H, W), got shape {mask.shape}")

    h, w = mask.shape
    rgb = image.convert("RGB")
    if rgb.size != (w, h):
        rgb = rgb.resize((w, h), Image.BILINEAR)

    base = np.asarray(rgb, dtype=np.float32)
    tint = colorize_mask(mask).astype(np.float32)

    # Only blend where we actually classified something worth showing.
    blend = (mask != Class.STRUCTURE)[..., None]
    out = np.where(blend, base * (1 - alpha) + tint * alpha, base)

    return Image.fromarray(out.round().clip(0, 255).astype(np.uint8))
