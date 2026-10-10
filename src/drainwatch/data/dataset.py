"""Torch dataset over the unified 3-class set.

The augmentation pipeline is the main lever against the domain gap: training images are
litter photographed in daylight on phones, while deployment images are Delhi drains seen
through traffic cameras — compressed, blurred, hazy, badly lit. The photometric
transforms below deliberately simulate that degradation, which matters more here than
any extra training epoch.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from drainwatch.config import (
    IMAGE_SIZE,
    IMAGENET_MEAN,
    IMAGENET_STD,
    NUM_CLASSES,
    PROCESSED_DIR,
    Class,
)

try:
    import albumentations as A
    import torch
    from albumentations.pytorch import ToTensorV2
    from torch.utils.data import Dataset
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "Training extras are required for this module. Install with:\n"
        "    pip install -e '.[train]'"
    ) from exc


def train_transforms(image_size: int = IMAGE_SIZE) -> A.Compose:
    """Geometric + degradation augmentation.

    The blur / compression / fog / low-light transforms are not decoration: deployment
    frames come from municipal CCTV through Delhi haze, and a model trained only on
    clean phone photos falls apart on them.

    Written against the **albumentations 2.x** API. The 1.x spellings differ in ways that
    are TypeErrors, not warnings — ``ShiftScaleRotate(mask_value=...)`` is now
    ``Affine(fill_mask=...)`` and ``ImageCompression(quality_lower=, quality_upper=)`` is
    now ``quality_range=``. Worth stating because every tutorial online still shows 1.x.
    """
    return A.Compose(
        [
            A.Resize(image_size, image_size),
            A.HorizontalFlip(p=0.5),
            # Affine replaces the deprecated ShiftScaleRotate. The ranges are the same
            # augmentation as before, just stated absolutely instead of as ± limits:
            # scale_limit=0.2 -> scale=(0.8, 1.2), shift_limit=0.06 -> ±6% translation.
            # fill_mask=STRUCTURE matters: corners rotated into frame must read as
            # background, never as water or waste, or the labels become wrong.
            A.Affine(
                translate_percent=(-0.06, 0.06),
                scale=(0.8, 1.2),
                rotate=(-15, 15),
                mask_interpolation=0,  # cv2.INTER_NEAREST — never interpolate labels
                fill_mask=int(Class.STRUCTURE),
                p=0.6,
            ),
            # Lighting: harsh noon sun through to dusk.
            A.RandomBrightnessContrast(brightness_limit=0.3, contrast_limit=0.3, p=0.7),
            A.HueSaturationValue(hue_shift_limit=10, sat_shift_limit=25, val_shift_limit=20, p=0.4),
            # Sensor and transmission degradation.
            A.OneOf([A.MotionBlur(blur_limit=7), A.GaussianBlur(blur_limit=(3, 7))], p=0.35),
            A.ImageCompression(quality_range=(40, 90), p=0.35),
            A.RandomFog(p=0.15),  # Delhi haze / smog
            A.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
            ToTensorV2(),
        ]
    )


def val_transforms(image_size: int = IMAGE_SIZE) -> A.Compose:
    """Deterministic: resize and normalize only."""
    return A.Compose(
        [
            A.Resize(image_size, image_size),
            A.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
            ToTensorV2(),
        ]
    )


class DrainSegmentationDataset(Dataset):
    """Image/mask pairs for one split of the unified dataset.

    Args:
        root: the ``data/processed`` directory produced by
            :func:`drainwatch.data.unify.build_unified_dataset`.
        split: ``"train"`` or ``"val"``, as keyed in ``splits.json``.
        image_size: square side length fed to the model.
        augment: apply :func:`train_transforms` instead of :func:`val_transforms`.

    Each item is ``{"pixel_values": (3, H, W) float32, "labels": (H, W) int64,
    "sample_id": str}``.
    """

    def __init__(
        self,
        root: Path = PROCESSED_DIR,
        split: str = "train",
        *,
        image_size: int = IMAGE_SIZE,
        augment: bool | None = None,
    ) -> None:
        self.root = Path(root)
        self.split = split

        splits_path = self.root / "splits.json"
        if not splits_path.exists():
            raise FileNotFoundError(
                f"{splits_path} not found — run `drainwatch data build` first"
            )
        splits = json.loads(splits_path.read_text())
        if split not in splits:
            raise KeyError(f"split {split!r} not in {sorted(splits)}")

        self.sample_ids: list[str] = splits[split]
        if augment is None:
            augment = split == "train"
        self.transform = (
            train_transforms(image_size) if augment else val_transforms(image_size)
        )

    def __len__(self) -> int:
        return len(self.sample_ids)

    def __getitem__(self, idx: int) -> dict:
        sample_id = self.sample_ids[idx]

        with Image.open(self.root / "images" / f"{sample_id}.jpg") as img:
            image = np.asarray(img.convert("RGB"))
        with Image.open(self.root / "masks" / f"{sample_id}.png") as msk:
            mask = np.asarray(msk, dtype=np.uint8)

        out = self.transform(image=image, mask=mask)
        return {
            "pixel_values": out["image"],
            "labels": out["mask"].long(),
            "sample_id": sample_id,
        }


def split_class_pixels(root: Path = PROCESSED_DIR, *, split: str = "train") -> np.ndarray:
    """Total pixels per class in one split, read from the manifest.

    Cheap — it never opens an image — which is what makes it usable as a pre-flight
    check before training rather than a diagnostic afterwards.
    """
    manifest = json.loads((Path(root) / "manifest.json").read_text())
    split_ids = set(json.loads((Path(root) / "splits.json").read_text())[split])

    totals = np.zeros(NUM_CLASSES, dtype=np.float64)
    for entry in manifest:
        if entry["sample_id"] not in split_ids:
            continue
        for cls, count in entry["class_pixels"].items():
            cls = int(cls)
            if cls < NUM_CLASSES:
                totals[cls] += count
    return totals


def class_weights(root: Path = PROCESSED_DIR, *, split: str = "train") -> torch.Tensor:
    """Inverse-frequency class weights from the manifest, for the training loss.

    ``waste`` is a small minority of pixels. Unweighted cross-entropy converges to
    predicting ``structure`` almost everywhere — high pixel accuracy, useless model,
    and a Choke Index pinned near zero. Weights are normalized to mean 1 so the loss
    scale (and therefore a sensible learning rate) is unchanged.
    """
    totals = split_class_pixels(root, split=split)

    present = totals > 0
    if not present.any():
        raise ValueError(f"no class pixels recorded for split {split!r}")

    weights = np.ones(NUM_CLASSES, dtype=np.float64)
    weights[present] = totals[present].sum() / (present.sum() * totals[present])
    weights /= weights.mean()  # keep the loss magnitude comparable to unweighted CE

    return torch.tensor(weights, dtype=torch.float32)
