"""Synthesise the one training signal the sources cannot supply: waste *in* water.

Why this module exists
----------------------
The Choke Index is ``waste / (waste + water)``. It is entirely a function of the
water-waste boundary. But no source dataset contains that boundary:

* TACO and UAVVaste label litter polygons as ``waste`` and leave everything else
  ``structure`` — so litter floating in a canal has its water labelled *structure*.
* ADE20K labels water as ``water`` and everything else ``structure`` — so litter on
  that water is labelled *water*.

So the two sources disagree about water, and critically **no single image ever contains
both ``water`` and ``waste``**. A model trained on them alone has to extrapolate to a
pixel configuration it has never once observed, which is precisely the configuration
every real reading consists of.

Copy-paste compositing fixes this directly: cut litter instances out of TACO photos and
paste them into the water region of ADE20K scenes. Both labels are then correct in the
same frame, with a genuine boundary between them. The technique is Ghiasi et al. 2021
(*Simple Copy-Paste is a Strong Data Augmentation Method*); here it is not decoration
but the only source of the supervision the product is scored on.

Two details decide whether this helps or actively hurts
------------------------------------------------------
**Feathered alpha.** A hard-edged paste leaves a 1-pixel discontinuity around every
object. That seam is far easier to detect than litter is, so the model learns "sharp
seam = waste" and collapses on real photographs that have no seams. The image is blended
with a *soft* alpha while the label mask is written from a *hard* threshold — labels stay
integral, the edge does not announce itself.

**Brightness harmonisation.** Litter cut from a bright beach photo pasted into a dim
river scene is separable on exposure alone — another shortcut. Each patch is pulled part
of the way toward the local mean of its destination.

Target-driven coverage
----------------------
Pasting onto water trades water pixels for waste pixels one-for-one, so when every paste
lands inside the channel the index reduces to ``covered / original_water_area``. That
makes the realised Choke Index directly steerable: sample a target, paste until it is
reached. The generated set therefore spans the whole decision range — including the
0.40 and 0.70 thresholds, where accuracy actually changes what a crew does.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from drainwatch.config import Class
from drainwatch.data.coco_masks import CocoIndex

MIN_DONOR_AREA = 256  # px; below this a pasted object is a few specks after downscaling
MAX_DONORS_PER_IMAGE = 4
# A pasted object may be scaled this small when a low Choke-Index target needs only a
# sliver of waste. Smaller than MIN_DONOR_AREA (which gates *extraction*): a fine target
# needs fine-grained control, and ~10x10 still survives the feather.
PASTE_MIN_AREA = 100
# Donors are held in memory all at once, and TACO's originals are up to 1537x2049 — a
# single large cut-out is ~9 MB, so a few thousand would be tens of gigabytes. Capping
# the long side here bounds the pool to a few hundred MB and costs nothing: a paste
# covers at most a fraction of a 640px frame, so anything larger is downscaled anyway.
MAX_DONOR_SIDE = 256
FEATHER_RADIUS = 1.2  # px of alpha blur; enough to kill the seam, not enough to smear
HARMONISE = 0.5  # 0 = paste as-is, 1 = fully match destination brightness
MAX_PASTE_ATTEMPTS = 40

# Target Choke-Index bands, cycled round-robin across composites so each severity is
# covered in equal measure rather than left to where uniform sampling happens to land.
# The high band aims deliberately past the 0.70 CRITICAL cutoff: pastes undershoot as
# they clip and overlap, so aiming at 0.70 would land in WATCH. Shared by the dataset
# build and the preview script so the preview reflects what training actually sees.
CHOKE_BANDS: tuple[tuple[float, float], ...] = ((0.05, 0.38), (0.42, 0.68), (0.74, 0.97))


def band_target(index: int, rng: random.Random) -> float:
    """A Choke-Index target for the ``index``-th composite, round-robin over the bands."""
    low, high = CHOKE_BANDS[index % len(CHOKE_BANDS)]
    return rng.uniform(low, high)



@dataclass(frozen=True)
class WasteDonor:
    """One litter instance cut out of a source photo, ready to paste."""

    patch: np.ndarray  # (h, w, 3) uint8 RGB
    alpha: np.ndarray  # (h, w) bool — True where the litter is
    origin: str

    @property
    def area(self) -> int:
        return int(self.alpha.sum())


def _instance_mask(polygons: Sequence[Sequence[float]], height: int, width: int) -> np.ndarray:
    """Rasterize one annotation's polygons into a boolean mask."""
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    for polygon in polygons:
        draw.polygon(list(polygon), fill=1)
    return np.asarray(canvas, dtype=bool)


def _cap_side(patch: np.ndarray, alpha: np.ndarray, max_side: int) -> tuple[np.ndarray, np.ndarray]:
    """Downscale a cut-out so its long side is at most ``max_side``.

    Applied at extraction, not paste time, because the whole donor pool is resident in
    memory at once. Alpha resamples NEAREST — it decides a label.
    """
    height, width = alpha.shape
    longest = max(height, width)
    if longest <= max_side:
        return patch, alpha

    scale = max_side / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    small_patch = np.asarray(
        Image.fromarray(patch).resize(size, Image.BILINEAR), dtype=np.uint8
    )
    small_alpha = (
        np.asarray(
            Image.fromarray(alpha.astype(np.uint8) * 255).resize(size, Image.NEAREST)
        )
        > 127
    )
    return small_patch, small_alpha


def iter_waste_donors(
    index: CocoIndex,
    images_root: Path,
    *,
    min_area: int = MIN_DONOR_AREA,
    max_per_image: int = MAX_DONORS_PER_IMAGE,
    max_side: int = MAX_DONOR_SIDE,
    limit: int | None = None,
    origin_prefix: str = "taco",
) -> Iterator[WasteDonor]:
    """Yield individual litter cut-outs from a COCO litter dataset.

    Instances rather than whole-image masks: a drain holds several distinct objects, and
    pasting one 400-polygon blob would produce a single implausible smear.

    ``max_per_image`` caps how many instances come from one photo, so a single
    heavily-annotated beach shot cannot dominate the donor pool and teach the model one
    brand of bottle. ``max_side`` bounds the memory the pool occupies — see
    :data:`MAX_DONOR_SIDE`.
    """
    yielded = 0
    for image_id in index.image_ids:
        if limit is not None and yielded >= limit:
            return

        annotations = index.anns_by_image.get(image_id, [])
        if not annotations:
            continue

        record = index.images[image_id]
        image_path = images_root / record["file_name"]
        if not image_path.exists():
            continue

        try:
            with Image.open(image_path) as handle:
                rgb = np.asarray(handle.convert("RGB"), dtype=np.uint8)
        except OSError:
            continue

        height, width = rgb.shape[:2]
        # The annotations' declared size can disagree with the downloaded file (TACO
        # serves a 640px Flickr variant). Rasterize at the real size or every polygon
        # lands in the wrong place.
        taken = 0
        for ann in annotations:
            if taken >= max_per_image or (limit is not None and yielded >= limit):
                break

            scale_x = width / float(record["width"])
            scale_y = height / float(record["height"])
            scaled = [
                [c * (scale_x if i % 2 == 0 else scale_y) for i, c in enumerate(polygon)]
                for polygon in ann["polygons"]
            ]

            alpha = _instance_mask(scaled, height, width)
            if alpha.sum() < min_area:
                continue

            rows = np.flatnonzero(alpha.any(axis=1))
            cols = np.flatnonzero(alpha.any(axis=0))
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            x0, x1 = int(cols[0]), int(cols[-1]) + 1

            patch, cut = _cap_side(
                rgb[y0:y1, x0:x1].copy(), alpha[y0:y1, x0:x1].copy(), max_side
            )
            # The cap can shrink a thin sliver below the usable area.
            if cut.sum() < 16:
                continue

            yield WasteDonor(
                patch=patch,
                alpha=cut,
                origin=f"{origin_prefix}:{record['file_name']}",
            )
            taken += 1
            yielded += 1


def _resize_donor(donor: WasteDonor, target_area: int, rng: random.Random) -> WasteDonor:
    """Scale a donor so its opaque area is near ``target_area``, with rotation.

    Alpha is resampled NEAREST throughout — it decides a label, and a half-opaque pixel
    has no meaningful class.
    """
    current = max(1, donor.area)
    scale = float(np.sqrt(target_area / current))
    height, width = donor.alpha.shape
    new_w = max(2, int(round(width * scale)))
    new_h = max(2, int(round(height * scale)))

    patch = Image.fromarray(donor.patch).resize((new_w, new_h), Image.BILINEAR)
    alpha = Image.fromarray(donor.alpha.astype(np.uint8) * 255).resize(
        (new_w, new_h), Image.NEAREST
    )

    if rng.random() < 0.5:
        patch = patch.transpose(Image.FLIP_LEFT_RIGHT)
        alpha = alpha.transpose(Image.FLIP_LEFT_RIGHT)

    # ``expand=True`` pads the corners, and the default pad is black. Those padded pixels
    # sit just outside the hard alpha but *inside* the feathered one, so a black pad
    # blends the background toward black and draws a dark fringe around every object —
    # reintroducing precisely the edge cue the feathering exists to remove. Padding with
    # the object's own mean colour makes the fringe indistinguishable from the object.
    fill = tuple(int(v) for v in np.asarray(patch).reshape(-1, 3).mean(axis=0))
    angle = rng.uniform(-35, 35)
    patch = patch.rotate(angle, resample=Image.BILINEAR, expand=True, fillcolor=fill)
    alpha = alpha.rotate(angle, resample=Image.NEAREST, expand=True)

    return WasteDonor(
        patch=np.asarray(patch, dtype=np.uint8),
        alpha=np.asarray(alpha) > 127,
        origin=donor.origin,
    )


def _harmonise(patch: np.ndarray, alpha: np.ndarray, destination: np.ndarray) -> np.ndarray:
    """Pull a patch partway toward the brightness of where it is going.

    Without this, exposure alone separates pasted litter from its background and the
    model learns the lighting discontinuity instead of the object.
    """
    if not alpha.any() or destination.size == 0:
        return patch

    src_mean = patch[alpha].reshape(-1, 3).mean(axis=0)
    dst_mean = destination.reshape(-1, 3).mean(axis=0)
    gain = np.where(src_mean > 1e-3, dst_mean / np.maximum(src_mean, 1e-3), 1.0)
    gain = 1.0 + HARMONISE * (gain - 1.0)
    return np.clip(patch.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def _feather(alpha: np.ndarray) -> np.ndarray:
    """Soft alpha in [0, 1] for blending. The label mask uses the hard alpha instead."""
    blurred = Image.fromarray(alpha.astype(np.uint8) * 255).filter(
        ImageFilter.GaussianBlur(FEATHER_RADIUS)
    )
    return np.asarray(blurred, dtype=np.float32) / 255.0


def _paste(
    rgb: np.ndarray,
    mask: np.ndarray,
    donor: WasteDonor,
    centre: tuple[int, int],
) -> int:
    """Composite one donor in place. Returns the number of pixels labelled waste.

    Clipped at the frame edge, which is realistic — waste piles run out of frame — and
    avoids rejecting every paste near a channel that touches the border.
    """
    cy, cx = centre
    dh, dw = donor.alpha.shape
    y0, x0 = cy - dh // 2, cx - dw // 2

    # Overlap of the patch rectangle with the frame, in both coordinate systems.
    fy0, fx0 = max(0, y0), max(0, x0)
    fy1, fx1 = min(rgb.shape[0], y0 + dh), min(rgb.shape[1], x0 + dw)
    if fy1 <= fy0 or fx1 <= fx0:
        return 0

    py0, px0 = fy0 - y0, fx0 - x0
    py1, px1 = py0 + (fy1 - fy0), px0 + (fx1 - fx0)

    hard = donor.alpha[py0:py1, px0:px1]
    if not hard.any():
        return 0

    region = rgb[fy0:fy1, fx0:fx1]
    patch = _harmonise(donor.patch[py0:py1, px0:px1], hard, region[hard])
    soft = _feather(donor.alpha)[py0:py1, px0:px1][..., None]

    rgb[fy0:fy1, fx0:fx1] = (region * (1.0 - soft) + patch * soft).astype(np.uint8)
    mask[fy0:fy1, fx0:fx1][hard] = int(Class.WASTE)
    return int(hard.sum())


def composite_waste_on_water(
    image: Image.Image,
    mask: np.ndarray,
    donors: Sequence[WasteDonor],
    *,
    rng: random.Random,
    target_choke: float,
    max_objects: int = 30,
) -> tuple[Image.Image, np.ndarray, float]:
    """Paste litter into the water region until the Choke Index reaches ``target_choke``.

    Args:
        image: an RGB scene whose ``mask`` marks real water.
        mask: (H, W) uint8 of ``Class.WATER`` / ``Class.STRUCTURE``.
        donors: litter cut-outs from :func:`iter_waste_donors`.
        rng: seeded, so a build is reproducible.
        target_choke: desired ``waste / (waste + water)`` in [0, 1).
        max_objects: hard cap, so an unreachable target cannot loop forever.

    Returns:
        ``(image, mask, realised_choke)``. The realised value is *measured* from the
        output rather than assumed — pastes clip at frame edges and overlap each other,
        so the request is a target, not a guarantee. Callers record the measured number.

    The loop is driven by the **measured** index recomputed each step, and each paste is
    sized by the pixel *deficit* to the target. An earlier version budgeted a fixed pixel
    count and stopped when that many waste pixels had been painted — but a paste spilling
    onto structure (which is realistic, and allowed) adds waste without removing water, so
    that count overshot the actual index and the realised value stalled around 0.71 for
    any high target. Driving on the measured index removes that bias; sizing by the
    deficit stops a single oversized first object from pinning low targets near 0.15.

    The paste centre is drawn uniformly from the water pixels, so an awkwardly shaped
    channel is handled without any geometry. Patches are free to spill onto structure:
    real waste piles against the edge of a drain, half on the concrete, and forbidding
    that would teach a tidiness that does not exist.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
    mask = mask.copy()

    water_px = int((mask == int(Class.WATER)).sum())
    if water_px == 0 or not donors:
        return Image.fromarray(rgb), mask, 0.0

    def measured_choke() -> tuple[float, int]:
        waste = int((mask == int(Class.WASTE)).sum())
        water = int((mask == int(Class.WATER)).sum())
        channel = waste + water
        return (waste / channel if channel else 0.0), waste

    for _ in range(max_objects):
        realised, waste_now = measured_choke()
        if realised >= target_choke:
            break
        water_sel = mask == int(Class.WATER)
        water_now = int(water_sel.sum())
        if water_now == 0:
            break

        # Size the next paste at the pixel deficit to the target, split into pieces (the
        # jitter) so the result reads as scattered debris rather than one blob. Deficit-
        # based rather than a fixed budget: it adapts as spill grows the channel, and it
        # keeps a low target from overshooting on an oversized first object.
        channel = waste_now + water_now
        deficit = max(0.0, target_choke * channel - waste_now)
        target_area = max(PASTE_MIN_AREA, int(deficit * rng.uniform(0.35, 0.75)))

        flat = np.flatnonzero(water_sel)
        index = int(flat[rng.randrange(len(flat))])
        centre = divmod(index, mask.shape[1])

        donor = _resize_donor(rng.choice(donors), target_area, rng)
        _paste(rgb, mask, donor, centre)

    realised, _ = measured_choke()
    return Image.fromarray(rgb), mask, realised
