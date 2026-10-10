"""Tests for waste-in-water compositing.

This is the only source of the water-waste boundary in the whole dataset, so a quiet bug
here does not produce an error — it produces a training set that teaches the wrong thing
and a model that scores well on validation and fails on a real drain. Hence the emphasis
below on label integrity and on the two shortcuts the model would otherwise learn
(the paste seam and the exposure discontinuity) rather than on "does it run".

Everything is synthetic and in-memory; no dataset download is needed.
"""

from __future__ import annotations

import random

import numpy as np
import pytest
from PIL import Image

from drainwatch.config import Class
from drainwatch.data.composite import (
    CHOKE_BANDS,
    WasteDonor,
    _harmonise,
    band_target,
    composite_waste_on_water,
    iter_waste_donors,
)


def water_scene(size=(120, 160), water_rows=slice(40, 100)):
    """An RGB scene with a horizontal band of 'water' and its matching label mask."""
    height, width = size
    rgb = np.full((height, width, 3), 70, dtype=np.uint8)  # dim scene
    rgb[water_rows] = (40, 90, 120)
    mask = np.full((height, width), int(Class.STRUCTURE), dtype=np.uint8)
    mask[water_rows] = int(Class.WATER)
    return Image.fromarray(rgb), mask


def donor(size=(20, 20), colour=(240, 60, 60)) -> WasteDonor:
    """A bright square cut-out — deliberately far from the scene's exposure."""
    patch = np.full((*size, 3), colour, dtype=np.uint8)
    return WasteDonor(patch=patch, alpha=np.ones(size, dtype=bool), origin="test")


# --- label integrity ------------------------------------------------------------


def test_only_the_three_known_labels_ever_appear():
    """The mask is a label array, not an image. A fourth value means a silent bug that
    the loss would happily train on."""
    image, mask = water_scene()

    _, out, _ = composite_waste_on_water(
        image, mask, [donor()], rng=random.Random(0), target_choke=0.5
    )

    assert set(np.unique(out)) <= {int(Class.STRUCTURE), int(Class.WATER), int(Class.WASTE)}


def test_the_label_mask_stays_hard_even_though_the_image_is_blended():
    """The seam fix blurs the *alpha*. If that blur reached the mask it would create
    fractional labels, and rounding them would invent class boundaries."""
    image, mask = water_scene()

    _, out, _ = composite_waste_on_water(
        image, mask, [donor()], rng=random.Random(1), target_choke=0.4
    )

    assert out.dtype == np.uint8
    # No intermediate values between class ids — a blurred mask would show 1s bleeding
    # into 2s as some third value, which the set check above catches, but also: every
    # waste pixel must be exactly WASTE.
    assert ((out == int(Class.WASTE)) | (out == int(Class.WATER)) | (out == 0)).all()


def test_water_pixels_are_consumed_not_duplicated():
    """Pasting over water must *replace* it. If waste were added without removing water
    the Choke Index denominator would grow and the score would read low."""
    image, mask = water_scene()
    water_before = int((mask == int(Class.WATER)).sum())

    _, out, _ = composite_waste_on_water(
        image, mask, [donor()], rng=random.Random(2), target_choke=0.6
    )

    water_after = int((out == int(Class.WATER)).sum())
    waste_after = int((out == int(Class.WASTE)).sum())
    assert water_after < water_before
    assert waste_after > 0


def test_the_input_mask_is_not_mutated():
    """``add_composite_source`` reuses each ADE sample's mask; mutating it in place would
    make every later composite inherit the previous one's waste."""
    image, mask = water_scene()
    original = mask.copy()

    composite_waste_on_water(image, mask, [donor()], rng=random.Random(3), target_choke=0.5)

    assert np.array_equal(mask, original)


# --- the Choke Index is actually steerable --------------------------------------


@pytest.mark.parametrize("target", [0.1, 0.3, 0.6, 0.85])
def test_realised_choke_tracks_the_target(target):
    """The point of targeting: the generated set must span the decision bands, including
    0.40 and 0.70 where the severity actually changes.

    The tolerance is wide on purpose. Pastes clip at frame edges and overlap each other,
    so the target is a request. What must hold is monotone tracking, not equality.
    """
    image, mask = water_scene()

    _, out, realised = composite_waste_on_water(
        image, mask, [donor(size=(14, 14))], rng=random.Random(4), target_choke=target
    )

    waste = int((out == int(Class.WASTE)).sum())
    water = int((out == int(Class.WATER)).sum())
    assert realised == pytest.approx(waste / (waste + water))
    assert abs(realised - target) < 0.25, f"asked {target}, got {realised}"


def test_realised_value_is_measured_not_assumed():
    """A target of 0.9 on a scene too small to fit that much waste must report what it
    actually achieved. The manifest records this number, and a fabricated one would
    mislabel every sample."""
    image, mask = water_scene(size=(40, 40), water_rows=slice(18, 22))

    _, out, realised = composite_waste_on_water(
        image, mask, [donor(size=(30, 30))], rng=random.Random(5), target_choke=0.95
    )

    waste = int((out == int(Class.WASTE)).sum())
    water = int((out == int(Class.WATER)).sum())
    assert realised == pytest.approx(waste / (waste + water)) if waste + water else True


def test_ascending_targets_give_ascending_coverage():
    """Monotonicity is what makes the target parameter meaningful at all."""
    results = []
    for target in (0.1, 0.4, 0.8):
        image, mask = water_scene()
        _, out, realised = composite_waste_on_water(
            image, mask, [donor(size=(12, 12))], rng=random.Random(7), target_choke=target
        )
        results.append(realised)

    assert results == sorted(results), results


def test_the_full_decision_range_is_reachable_not_compressed_to_the_middle():
    """The regression this targeting rewrite fixed.

    The pixel-budget loop stalled near 0.71 for any high target (spill onto structure
    inflated the painted-pixel count without lowering water) and floored near 0.15 for
    any low target (the smallest donor was too big). Both ends collapsed toward WATCH,
    starving the CRITICAL band the product turns on. Driving on the measured index and
    sizing by the deficit must put a low target clearly below the 0.40 WATCH cutoff and a
    high target clearly into CRITICAL (>=0.70).
    """
    low_img, low_mask = water_scene()
    _, _, low = composite_waste_on_water(
        low_img, low_mask, [donor(size=(16, 16))], rng=random.Random(1), target_choke=0.15
    )
    high_img, high_mask = water_scene()
    _, _, high = composite_waste_on_water(
        high_img, high_mask, [donor(size=(16, 16))], rng=random.Random(1), target_choke=0.85
    )

    assert low < 0.40, f"low target did not clear the WATCH floor: {low}"
    assert high >= 0.70, f"high target did not reach CRITICAL: {high}"


# --- the shortcuts the model must not be able to learn --------------------------


def _boundary_ring(waste: np.ndarray) -> np.ndarray:
    """The one-pixel ring immediately outside a label region."""
    padded = np.zeros_like(waste)
    padded[1:-1, 1:-1] = (
        waste[:-2, 1:-1] | waste[2:, 1:-1] | waste[1:-1, :-2] | waste[1:-1, 2:]
    )
    return padded & ~waste


def test_the_pasted_edge_is_feathered_not_a_hard_step():
    """A 1-pixel discontinuity around every object is far easier to detect than litter.
    Trained on hard-edged pastes the model learns "seam = waste" and collapses on real
    photographs, which have no seams. So the image must show blended values at the
    boundary even though the label mask does not.

    The discriminating property is *not* "the ring is brighter" — harmonisation pulls the
    patch toward its destination, so the blended values land close to the background. It
    is that ring pixels differ from the untouched background at all: under a hard step,
    every pixel outside the alpha is bit-identical to the input.
    """
    image, mask = water_scene()
    background_reds = {40, 70}  # pure water, pure structure

    out_image, out_mask, _ = composite_waste_on_water(
        image, mask, [donor(size=(24, 24))], rng=random.Random(11), target_choke=0.2
    )
    rgb = np.asarray(out_image)

    ring = _boundary_ring(out_mask == int(Class.WASTE))
    assert ring.any(), "no boundary found — the test scene is wrong"

    blended = set(np.unique(rgb[..., 0][ring]).tolist()) - background_reds
    assert blended, (
        "every boundary pixel is still exactly a background value, so the alpha is being "
        "applied as a hard step"
    )


def test_rotation_does_not_draw_a_dark_fringe_around_objects():
    """``Image.rotate(expand=True)`` pads with black by default. Those pads fall outside
    the hard alpha but inside the feathered one, so they blend the background toward
    black — a dark outline around every pasted object, which is the same learnable edge
    cue the feathering exists to destroy.

    Caught by the test above reporting boundary reds *below* the water's own value, which
    no blend toward a brighter patch can produce.
    """
    image, mask = water_scene()
    darkest_background = 40  # water's red channel; structure is 70

    out_image, out_mask, _ = composite_waste_on_water(
        image, mask, [donor(size=(24, 24), colour=(240, 60, 60))], rng=random.Random(11),
        target_choke=0.2,
    )
    rgb = np.asarray(out_image)

    ring = _boundary_ring(out_mask == int(Class.WASTE))
    ring_reds = rgb[..., 0][ring]
    # The donor harmonises to a red well above 40, so a correct blend can only raise the
    # water's red. Anything materially below it is pad bleeding through.
    assert ring_reds.min() >= darkest_background - 2, (
        f"boundary reds dip to {ring_reds.min()}, below the darkest background "
        f"({darkest_background}) — rotation padding is bleeding into the blend"
    )


def test_harmonise_pulls_a_bright_patch_toward_a_dim_destination():
    """Exposure alone would otherwise separate pasted litter from its background."""
    patch = np.full((8, 8, 3), 240, dtype=np.uint8)
    alpha = np.ones((8, 8), dtype=bool)
    destination = np.full((64, 3), 60, dtype=np.uint8)  # much darker

    out = _harmonise(patch, alpha, destination)

    assert out.mean() < patch.mean(), "the patch was not darkened at all"
    assert out.mean() > destination.mean(), "fully matching destroys the object's identity"


def test_harmonise_is_safe_on_a_black_patch():
    """A zero-mean patch would divide by zero in the naive formulation."""
    patch = np.zeros((4, 4, 3), dtype=np.uint8)
    alpha = np.ones((4, 4), dtype=bool)

    out = _harmonise(patch, alpha, np.full((16, 3), 200, dtype=np.uint8))

    assert np.isfinite(out).all()
    assert out.dtype == np.uint8


# --- degenerate inputs must not raise -------------------------------------------


def test_a_scene_with_no_water_is_returned_untouched():
    """ADE images are filtered for water, but a frame whose water vanishes after resize
    must not crash a 2,000-sample build."""
    rgb = Image.fromarray(np.full((30, 30, 3), 100, dtype=np.uint8))
    mask = np.full((30, 30), int(Class.STRUCTURE), dtype=np.uint8)

    out_image, out_mask, realised = composite_waste_on_water(
        rgb, mask, [donor()], rng=random.Random(13), target_choke=0.5
    )

    assert realised == 0.0
    assert np.array_equal(out_mask, mask)
    assert np.array_equal(np.asarray(out_image), np.asarray(rgb))


def test_no_donors_is_not_an_error():
    image, mask = water_scene()

    _, out, realised = composite_waste_on_water(
        image, mask, [], rng=random.Random(17), target_choke=0.5
    )

    assert realised == 0.0
    assert int((out == int(Class.WASTE)).sum()) == 0


def test_a_donor_larger_than_the_frame_is_clipped_not_rejected():
    """Waste piles run out of frame in real photographs. Rejecting every oversized paste
    would make composites impossible on a channel that touches the border."""
    image, mask = water_scene(size=(40, 40), water_rows=slice(10, 30))

    _, out, _ = composite_waste_on_water(
        image, mask, [donor(size=(200, 200))], rng=random.Random(19), target_choke=0.5
    )

    assert int((out == int(Class.WASTE)).sum()) > 0
    assert out.shape == (40, 40)


def test_target_of_zero_pastes_nothing():
    image, mask = water_scene()

    _, out, realised = composite_waste_on_water(
        image, mask, [donor()], rng=random.Random(23), target_choke=0.0
    )

    assert realised == 0.0
    assert int((out == int(Class.WASTE)).sum()) == 0


# --- band-stratified targeting (shared by build and preview) --------------------


def test_band_target_cycles_through_every_band():
    """Round-robin, so a run of composites covers ok/watch/critical in equal measure
    rather than clustering wherever uniform sampling lands. This is what the build and
    the preview both call, so they cannot drift apart."""
    rng = random.Random(0)
    draws = [band_target(i, rng) for i in range(len(CHOKE_BANDS) * 4)]

    for i, value in enumerate(draws):
        low, high = CHOKE_BANDS[i % len(CHOKE_BANDS)]
        assert low <= value <= high, f"draw {i}={value} outside band {(low, high)}"


def test_the_high_band_aims_past_the_critical_cutoff():
    """Pastes undershoot, so the high band must aim beyond 0.70 or it lands in WATCH and
    CRITICAL stays empty — the exact failure this stratification exists to prevent."""
    high_low, _ = CHOKE_BANDS[2]

    assert high_low > 0.70, f"high band starts at {high_low}, not past the 0.70 cutoff"


def test_a_seeded_rng_makes_the_build_reproducible():
    """A build that cannot be reproduced cannot be debugged when IoU comes out wrong."""
    first = composite_waste_on_water(
        *water_scene(), [donor()], rng=random.Random(99), target_choke=0.5
    )
    second = composite_waste_on_water(
        *water_scene(), [donor()], rng=random.Random(99), target_choke=0.5
    )

    assert np.array_equal(first[1], second[1])
    assert first[2] == second[2]


# --- donor extraction -----------------------------------------------------------


def test_donors_are_rescaled_when_the_downloaded_image_is_smaller(tmp_path):
    """TACO serves a 640px Flickr variant while its annotations describe the original.
    Rasterizing at the declared size would put every polygon in the wrong place — the
    kind of bug that yields a trainable dataset full of litter labels on empty sky.
    """
    from drainwatch.data.coco_masks import CocoIndex

    # Declared 200x200, actual file 100x100: a 2x shrink.
    Image.fromarray(np.full((100, 100, 3), 128, dtype=np.uint8)).save(tmp_path / "a.jpg")
    index = CocoIndex(
        images={1: {"id": 1, "file_name": "a.jpg", "height": 200, "width": 200}},
        anns_by_image={1: [{"category_id": 1, "polygons": [[20, 20, 180, 20, 180, 180, 20, 180]]}]},
    )

    donors = list(iter_waste_donors(index, tmp_path, min_area=100))

    assert len(donors) == 1
    # The polygon covers 160/200 = 80% of each declared axis, so after rescaling it must
    # cover ~80% of the 100px file, i.e. ~80x80 — not 160x160, which would not fit.
    height, width = donors[0].alpha.shape
    assert 70 <= height <= 90, height
    assert 70 <= width <= 90, width


def test_tiny_instances_are_dropped(tmp_path):
    """A 10-pixel polygon becomes a few specks after downscaling and adds label noise."""
    from drainwatch.data.coco_masks import CocoIndex

    Image.fromarray(np.full((100, 100, 3), 128, dtype=np.uint8)).save(tmp_path / "a.jpg")
    index = CocoIndex(
        images={1: {"id": 1, "file_name": "a.jpg", "height": 100, "width": 100}},
        anns_by_image={1: [{"category_id": 1, "polygons": [[1, 1, 4, 1, 4, 4, 1, 4]]}]},
    )

    assert list(iter_waste_donors(index, tmp_path, min_area=256)) == []


def test_one_photo_cannot_dominate_the_donor_pool(tmp_path):
    """Without a per-image cap, a single heavily-annotated beach shot supplies hundreds
    of donors and every composite is pasted with the same brand of bottle."""
    from drainwatch.data.coco_masks import CocoIndex

    Image.fromarray(np.full((100, 100, 3), 128, dtype=np.uint8)).save(tmp_path / "a.jpg")
    polygon = [[10, 10, 60, 10, 60, 60, 10, 60]]
    index = CocoIndex(
        images={1: {"id": 1, "file_name": "a.jpg", "height": 100, "width": 100}},
        anns_by_image={1: [{"category_id": 1, "polygons": polygon} for _ in range(20)]},
    )

    donors = list(iter_waste_donors(index, tmp_path, max_per_image=3))

    assert len(donors) == 3


def test_a_missing_image_file_is_skipped(tmp_path):
    """TACO's Flickr links rot; a dead link must not abort donor collection."""
    from drainwatch.data.coco_masks import CocoIndex

    index = CocoIndex(
        images={1: {"id": 1, "file_name": "gone.jpg", "height": 100, "width": 100}},
        anns_by_image={1: [{"category_id": 1, "polygons": [[10, 10, 60, 10, 60, 60]]}]},
    )

    assert list(iter_waste_donors(index, tmp_path)) == []


def test_donor_memory_is_bounded_by_a_side_cap(tmp_path):
    """The whole donor pool is resident at once, and TACO's originals reach 1537x2049 —
    one uncapped cut-out is ~9 MB, so a few thousand would exhaust memory during
    ``data build``. Pastes cover a fraction of a 640px frame, so the cap costs nothing.
    """
    from drainwatch.data.coco_masks import CocoIndex

    Image.fromarray(np.full((1200, 1600, 3), 128, dtype=np.uint8)).save(tmp_path / "big.jpg")
    index = CocoIndex(
        images={1: {"id": 1, "file_name": "big.jpg", "height": 1200, "width": 1600}},
        anns_by_image={
            1: [{"category_id": 1, "polygons": [[50, 50, 1550, 50, 1550, 1150, 50, 1150]]}]
        },
    )

    (only,) = list(iter_waste_donors(index, tmp_path, max_side=256))

    assert max(only.alpha.shape) <= 256
    assert only.patch.shape[:2] == only.alpha.shape
    assert only.patch.nbytes < 256 * 256 * 3 + 1


def test_the_side_cap_does_not_produce_empty_donors(tmp_path):
    """A thin sliver can shrink below one pixel of coverage under an aggressive cap; an
    empty donor would be pasted as a no-op forever and stall the target loop."""
    from drainwatch.data.coco_masks import CocoIndex

    Image.fromarray(np.full((900, 900, 3), 128, dtype=np.uint8)).save(tmp_path / "a.jpg")
    index = CocoIndex(
        images={1: {"id": 1, "file_name": "a.jpg", "height": 900, "width": 900}},
        anns_by_image={1: [{"category_id": 1, "polygons": [[0, 0, 890, 0, 890, 3, 0, 3]]}]},
    )

    for found in iter_waste_donors(index, tmp_path, max_side=32):
        assert found.area > 0, "a donor with no opaque pixels was emitted"
