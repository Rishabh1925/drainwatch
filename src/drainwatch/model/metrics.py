"""Segmentation metrics.

numpy-only and free of torch so the metric arithmetic is unit-testable on its own —
IoU bookkeeping is easy to get subtly wrong, and a silent error here would misreport
whether the model clears its quality gate.
"""

from __future__ import annotations

import numpy as np

from drainwatch.config import NUM_CLASSES, Class

# Quality gate from the project plan. Below this, the Choke Index is not trustworthy
# and the fallback is binary blocked/clear classification.
GATE = {Class.WASTE: 0.45, Class.WATER: 0.60}


class ConfusionMatrix:
    """Accumulating confusion matrix over batches. Rows are truth, columns prediction."""

    def __init__(self, num_classes: int = NUM_CLASSES) -> None:
        self.num_classes = num_classes
        self.matrix = np.zeros((num_classes, num_classes), dtype=np.int64)

    def update(self, target: np.ndarray, pred: np.ndarray) -> None:
        """Accumulate one batch. Arrays may be any shape as long as they match."""
        if target.shape != pred.shape:
            raise ValueError(f"shape mismatch: target {target.shape} vs pred {pred.shape}")

        t = target.reshape(-1).astype(np.int64)
        p = pred.reshape(-1).astype(np.int64)

        # Drop anything outside the label range (e.g. 255 ignore regions).
        valid = (t >= 0) & (t < self.num_classes) & (p >= 0) & (p < self.num_classes)
        t, p = t[valid], p[valid]

        flat = np.bincount(
            t * self.num_classes + p, minlength=self.num_classes**2
        ).reshape(self.num_classes, self.num_classes)
        self.matrix += flat

    def iou_per_class(self) -> np.ndarray:
        """IoU per class. NaN for classes absent from both truth and prediction."""
        tp = np.diag(self.matrix).astype(np.float64)
        union = self.matrix.sum(axis=1) + self.matrix.sum(axis=0) - tp
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(union > 0, tp / union, np.nan)

    def miou(self) -> float:
        """Mean IoU over classes that actually appear. NaN if none do."""
        iou = self.iou_per_class()
        # Short-circuit instead of letting np.nanmean warn: an all-NaN array is the
        # legitimate "nothing has been accumulated yet" state, not an anomaly.
        if np.all(np.isnan(iou)):
            return float("nan")
        return float(np.nanmean(iou))

    def pixel_accuracy(self) -> float:
        total = self.matrix.sum()
        return float(np.diag(self.matrix).sum() / total) if total else float("nan")

    def meets_gate(self, gate: dict = GATE) -> bool:
        """Whether per-class IoU clears the project's quality gate."""
        iou = self.iou_per_class()
        return all(
            not np.isnan(iou[cls]) and iou[cls] >= threshold for cls, threshold in gate.items()
        )

    def report(self) -> str:
        """Readable summary: per-class IoU, mIoU, pixel accuracy, gate verdict."""
        iou = self.iou_per_class()
        lines = ["per-class IoU:"]
        for cls in range(self.num_classes):
            name = Class(cls).name.lower()
            value = "   n/a" if np.isnan(iou[cls]) else f"{iou[cls]:6.3f}"
            flag = ""
            if cls in GATE and not np.isnan(iou[cls]):
                flag = "  OK" if iou[cls] >= GATE[cls] else f"  BELOW GATE ({GATE[cls]:.2f})"
            lines.append(f"  {name:<10} {value}{flag}")
        lines.append(f"mIoU            {self.miou():6.3f}")
        lines.append(f"pixel accuracy  {self.pixel_accuracy():6.3f}")
        lines.append(f"gate            {'PASS' if self.meets_gate() else 'FAIL'}")
        return "\n".join(lines)
