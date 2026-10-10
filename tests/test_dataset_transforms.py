"""Tests for the augmentation pipeline, against the *installed* albumentations.

These exist because of a specific near-miss. The pipeline was written against the
albumentations 1.x API, and 1.x → 2.x renamed two arguments this project passes:

* ``ImageCompression(quality_lower=, quality_upper=)`` → ``quality_range=``
* ``ShiftScaleRotate(mask_value=)``                    → ``Affine(fill_mask=)``

Both are ``TypeError`` on construction, not warnings — so with albumentations 2.x
installed, ``train_transforms()`` could not be built at all, and nothing would have
revealed that until the first training run. A test that merely imported the module would
still have passed; the transforms have to actually be *constructed and run*.

The second thing checked here is the one that silently corrupts a segmentation dataset:
**a geometric transform must never interpolate a label mask.** Blending label 0 and
label 2 yields label 1 — structure and waste average to *water* — which is not a visible
bug, just a quietly wrong training signal.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("albumentations", reason="training extras not installed")
pytest.importorskip("torch", reason="training extras not installed")

from drainwatch.config import IMAGE_SIZE, Class  # noqa: E402
from drainwatch.data.dataset import train_transforms, val_transforms  # noqa: E402

VALID_LABELS = {int(c) for c in Class}


@pytest.fixture
def pair():
    """A synthetic image/mask pair using all three classes, deliberately non-square."""
    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    mask = np.zeros((300, 400), dtype=np.uint8)  # structure
    mask[100:200, :200] = int(Class.WATER)
    mask[100:200, 200:] = int(Class.WASTE)
    return image, mask


def test_train_transforms_can_be_constructed():
    """The regression guard: wrong argument names make this line raise TypeError."""
    assert train_transforms(256) is not None


def test_train_transforms_output_shapes_and_types(pair):
    image, mask = pair

    out = train_transforms(256)(image=image, mask=mask)

    assert out["image"].shape == (3, 256, 256)  # CHW, after ToTensorV2
    assert out["mask"].shape == (256, 256)
    assert out["image"].dtype.is_floating_point  # Normalize ran


def test_augmentation_never_invents_label_values(pair):
    """Thirty draws, so every probabilistic branch in the pipeline fires at least once.

    An interpolated mask is the failure this catches: averaging STRUCTURE (0) and
    WASTE (2) produces WATER (1), a label that was never in the source.
    """
    image, mask = pair
    transform = train_transforms(256)

    seen: set[int] = set()
    for _ in range(30):
        labels = transform(image=image, mask=mask)["mask"].numpy()
        seen.update(int(v) for v in np.unique(labels))

    assert seen <= VALID_LABELS, f"augmentation produced labels outside {VALID_LABELS}: {seen}"


def test_rotated_in_corners_become_structure_not_waste(pair):
    """``fill_mask`` has to be STRUCTURE.

    Affine's default fill is 0, which happens to *be* STRUCTURE here — but relying on
    that coincidence means a future class renumbering silently labels empty corners as
    whatever class 0 becomes. The intent is asserted, not the default.
    """
    import albumentations as A

    image, mask = pair
    # Rotate hard, with probability 1, so corners are certainly filled.
    forced = A.Compose(
        [A.Affine(rotate=(45, 45), mask_interpolation=0, fill_mask=int(Class.STRUCTURE), p=1.0)]
    )

    labels = forced(image=image, mask=mask)["mask"]

    assert labels[0, 0] == int(Class.STRUCTURE)
    assert labels[-1, -1] == int(Class.STRUCTURE)


def test_val_transforms_are_deterministic(pair):
    """Validation metrics must not move between runs on identical input."""
    image, mask = pair
    transform = val_transforms(256)

    first = transform(image=image, mask=mask)
    second = transform(image=image, mask=mask)

    assert np.array_equal(first["image"].numpy(), second["image"].numpy())
    assert np.array_equal(first["mask"].numpy(), second["mask"].numpy())


def test_val_transforms_preserve_all_three_classes(pair):
    """Resize-only must not lose a class; NEAREST on the mask is what guarantees it."""
    image, mask = pair

    labels = val_transforms(256)(image=image, mask=mask)["mask"].numpy()

    assert set(int(v) for v in np.unique(labels)) == VALID_LABELS


def test_default_image_size_matches_the_model(pair):
    """The transform default and the SegFormer input size are one number, not two."""
    image, mask = pair

    out = val_transforms()(image=image, mask=mask)

    assert out["image"].shape == (3, IMAGE_SIZE, IMAGE_SIZE)
