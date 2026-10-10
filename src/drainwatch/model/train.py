"""Fine-tuning loop for SegFormer on the unified 3-class dataset.

Runs on Apple silicon via MPS. Two deliberate choices:

*The loss is computed here rather than by passing ``labels=`` to the model.*
``SegformerForSemanticSegmentation`` computes an **unweighted** cross-entropy internally,
which on this dataset converges to predicting ``structure`` almost everywhere — 90%+
pixel accuracy and a useless model. Class weights are essential, so the loss is explicit.

*The best checkpoint is chosen by water/waste IoU, not mIoU.*
``structure`` is the easy majority class and inflates mIoU. A checkpoint that wins on
mIoU can be worse at the only two classes the Choke Index actually reads.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from drainwatch.config import CHECKPOINT_DIR, IMAGE_SIZE, PROCESSED_DIR, Class
from drainwatch.model.evaluate import evaluate_model
from drainwatch.model.metrics import ConfusionMatrix


@dataclass
class TrainConfig:
    """Hyperparameters. Defaults are tuned for SegFormer-b0 on an M-series laptop.

    ``lr`` of 6e-5 is the SegFormer paper's fine-tuning rate; the encoder is pretrained
    and larger steps wash out the ADE20K features that make this work with so little data.
    """

    epochs: int = 20
    batch_size: int = 4  # 512x512 b0 fits comfortably in unified memory at this size
    lr: float = 6e-5
    weight_decay: float = 0.01
    image_size: int = IMAGE_SIZE
    num_workers: int = 2
    patience: int = 6  # stop early; a time-boxed build cannot afford dead epochs
    seed: int = 42
    poly_power: float = 1.0  # linear decay, as in the SegFormer reference schedule


def _set_seed(seed: int) -> None:
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _score(confusion: ConfusionMatrix) -> float:
    """Checkpoint-selection score: mean IoU over water and waste only.

    See the module docstring — ``structure`` must not get a vote here. Returns NaN when
    neither class appears anywhere in the validation split, which the caller treats as
    a fatal dataset error rather than a bad epoch.
    """
    iou = confusion.iou_per_class()
    pair = [iou[Class.WATER], iou[Class.WASTE]]
    if all(np.isnan(v) for v in pair):
        return float("nan")
    return float(np.nanmean(pair))


def train(
    *,
    root: Path = PROCESSED_DIR,
    out_dir: Path = CHECKPOINT_DIR,
    config: TrainConfig | None = None,
    device: torch.device | None = None,
    base_model: str | None = None,
    model=None,
) -> dict:
    """Fine-tune and write the best checkpoint to ``out_dir``.

    Args:
        model: a pre-constructed model to fine-tune. Normally omitted — the pretrained
            base is downloaded instead. Supplying one lets the loop be exercised with a
            randomly initialised net, offline, which is how the training path gets
            smoke-tested without a multi-hour run or a model download.

    Returns a training report (also written as ``training_report.json``) holding the
    per-epoch history and the best epoch's metrics.
    """
    from drainwatch.data.dataset import (
        DrainSegmentationDataset,
        class_weights,
        split_class_pixels,
    )
    from drainwatch.model.segformer import load_model, pick_device

    config = config or TrainConfig()
    device = device or pick_device()
    _set_seed(config.seed)

    train_set = DrainSegmentationDataset(root, "train", image_size=config.image_size)
    val_set = DrainSegmentationDataset(root, "val", image_size=config.image_size, augment=False)
    if not len(train_set) or not len(val_set):
        raise ValueError(
            f"empty split: train={len(train_set)} val={len(val_set)} — "
            "run `drainwatch data build` first"
        )

    # Pre-flight: checkpoint selection scores water+waste IoU only, so a validation split
    # without either class cannot rank one epoch above another — the run would train to
    # completion and the "best" checkpoint would be whichever epoch happened to come
    # first. Reading the manifest costs milliseconds; discovering this after 20 epochs
    # costs a day. (Note the metric alone cannot catch it: an untrained model *predicts*
    # water and waste even when the truth holds none, which yields IoU 0.0, not NaN.)
    val_pixels = split_class_pixels(root, split="val")
    missing = [
        Class(c).name.lower() for c in (Class.WATER, Class.WASTE) if val_pixels[c] == 0
    ]
    if len(missing) == len(("water", "waste")):
        raise ValueError(
            "validation split contains no water or waste pixels — checkpoint selection "
            "would be arbitrary. Check the class balance from `drainwatch data build`."
        )
    if missing:
        print(f"warning: validation split has no {', '.join(missing)} pixels — "
              f"its IoU will be 0 and the quality gate cannot pass")

    # Workers are pointless on MPS for a dataset this small and spawn cost dominates;
    # pin_memory is a CUDA-only optimisation.
    workers = config.num_workers if device.type == "cuda" else 0
    train_loader = DataLoader(
        train_set,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=workers,
        pin_memory=device.type == "cuda",
        drop_last=len(train_set) > config.batch_size,
    )
    val_loader = DataLoader(
        val_set, batch_size=config.batch_size, shuffle=False, num_workers=workers
    )

    if model is None:
        model = load_model(base=base_model) if base_model else load_model()
    model = model.to(device)

    weights = class_weights(root).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.lr, weight_decay=config.weight_decay
    )
    total_steps = max(1, config.epochs * len(train_loader))
    scheduler = torch.optim.lr_scheduler.PolynomialLR(
        optimizer, total_iters=total_steps, power=config.poly_power
    )

    print(f"device={device}  train={len(train_set)}  val={len(val_set)}")
    print(f"class weights: {weights.tolist()}")

    out_dir.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    best_score = -1.0
    best_epoch = -1
    epochs_since_best = 0

    for epoch in range(1, config.epochs + 1):
        model.train()
        started = time.monotonic()
        running = 0.0

        for step, batch in enumerate(train_loader, start=1):
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            # ``.contiguous()`` twice is deliberate, not redundant. The MPS backward pass
            # reaches for a ``.view()`` somewhere in this path (the error is raised from
            # loss.backward(), *after* the forward and the loss both succeeded), and a
            # view is only valid on a contiguous buffer — so anywhere the graph records
            # non-contiguous strides, that view can blow up. Forcing contiguity both after
            # the model (whose decode head concatenates upsampled stage features, giving a
            # non-contiguous output) and after the interpolate (bilinear upsampling emits
            # a strided tensor) leaves the backward nothing strided to view. On CPU/CUDA
            # this is a no-op.
            logits = model(pixel_values=pixel_values).logits.contiguous()
            # SegFormer emits logits at 1/4 resolution; upsample to the label grid.
            logits = torch.nn.functional.interpolate(
                logits, size=labels.shape[-2:], mode="bilinear", align_corners=False
            ).contiguous()
            # Flatten to (N, C) / (N,) ourselves with reshape rather than handing the 4-D
            # spatial tensors to cross_entropy. Doing the flatten here with reshape (which
            # copies when strides demand it) also sidesteps any internal view.
            num_classes = logits.shape[1]
            loss = torch.nn.functional.cross_entropy(
                logits.permute(0, 2, 3, 1).reshape(-1, num_classes),
                labels.reshape(-1),
                weight=weights,
                ignore_index=255,
            )

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            scheduler.step()

            running += loss.item()
            if step % 20 == 0:
                print(f"  epoch {epoch} step {step}/{len(train_loader)} loss {running/step:.4f}")

        train_loss = running / max(1, len(train_loader))
        confusion = evaluate_model(model, val_loader, device)
        score = _score(confusion)
        iou = confusion.iou_per_class()

        # Backstop for the same condition the pre-flight check catches, in the one shape
        # it cannot see: a model that has collapsed to predicting only `structure` while
        # the validation split also happens to hold neither class. Then both IoUs really
        # are NaN, `score > best_score` is False forever, and no checkpoint is ever saved
        # — but the loop would still finish and write a report claiming success.
        if np.isnan(score):
            raise ValueError(
                "validation IoU is undefined for both water and waste — no checkpoint "
                "can be selected. The model is predicting only `structure`."
            )

        entry = {
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "miou": round(confusion.miou(), 4),
            "score": round(score, 4),
            "iou": {Class(c).name.lower(): None if np.isnan(iou[c]) else round(float(iou[c]), 4)
                    for c in range(len(Class))},
            "seconds": round(time.monotonic() - started, 1),
        }
        history.append(entry)
        print(
            f"epoch {epoch:>2}  loss {train_loss:.4f}  mIoU {confusion.miou():.3f}  "
            f"score {score:.3f}  ({entry['seconds']:.0f}s)"
        )

        if score > best_score:
            best_score, best_epoch, epochs_since_best = score, epoch, 0
            model.save_pretrained(out_dir / "best")
            print(f"  ↳ new best (score {score:.3f}) saved to {out_dir / 'best'}")
        else:
            epochs_since_best += 1
            if epochs_since_best >= config.patience:
                print(f"no improvement in {config.patience} epochs — stopping early")
                break

    # The final confusion matrix belongs to the last epoch, not necessarily the best one.
    report = {
        "config": asdict(config),
        "device": str(device),
        "train_samples": len(train_set),
        "val_samples": len(val_set),
        "best_epoch": best_epoch,
        "best_score": round(best_score, 4),
        "history": history,
        "checkpoint": str(out_dir / "best"),
    }
    (out_dir / "training_report.json").write_text(json.dumps(report, indent=2))
    print(f"\nbest epoch {best_epoch} (score {best_score:.3f}) -> {out_dir / 'best'}")
    return report
