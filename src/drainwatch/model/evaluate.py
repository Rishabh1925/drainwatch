"""Evaluation: per-class IoU and confusion matrix.

Shared by training (per-epoch validation) and the ``drainwatch eval`` command. The plan
calls for the held-out public split and the hand-annotated Delhi set to be reported
*separately* — same function, different ``root``, because a single blended number would
hide exactly the thing worth knowing: how much the model loses crossing the domain gap.
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch.utils.data import DataLoader

from drainwatch.config import IMAGE_SIZE, NUM_CLASSES, PROCESSED_DIR
from drainwatch.model.metrics import ConfusionMatrix


@torch.inference_mode()
def evaluate_model(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
    *,
    num_classes: int = NUM_CLASSES,
) -> ConfusionMatrix:
    """Accumulate a confusion matrix over a dataloader.

    Logits are upsampled to the label resolution before argmax — comparing a quarter-res
    prediction against a full-res mask would silently measure the wrong thing.
    """
    model = model.to(device).eval()
    confusion = ConfusionMatrix(num_classes)

    for batch in loader:
        pixel_values = batch["pixel_values"].to(device)
        labels = batch["labels"]

        logits = model(pixel_values=pixel_values).logits
        logits = torch.nn.functional.interpolate(
            logits, size=labels.shape[-2:], mode="bilinear", align_corners=False
        )
        preds = logits.argmax(dim=1).to("cpu")

        confusion.update(labels.numpy(), preds.numpy())

    return confusion


def evaluate_checkpoint(
    checkpoint: Path,
    *,
    root: Path = PROCESSED_DIR,
    split: str = "val",
    batch_size: int = 4,
    image_size: int = IMAGE_SIZE,
    device: torch.device | None = None,
) -> ConfusionMatrix:
    """Load a checkpoint and score it on one split. Used by ``drainwatch eval``."""
    from drainwatch.data.dataset import DrainSegmentationDataset
    from drainwatch.model.segformer import load_model, pick_device

    device = device or pick_device()
    model = load_model(checkpoint)
    dataset = DrainSegmentationDataset(root, split, image_size=image_size, augment=False)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)

    return evaluate_model(model, loader, device)
