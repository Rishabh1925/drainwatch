"""Tests for the metric bookkeeping.

IoU accounting is easy to get subtly wrong and a mistake here would misreport whether
the model clears its gate — which is the decision that drives the whole schedule.
"""

from __future__ import annotations

import numpy as np
import pytest

from drainwatch.config import Class
from drainwatch.model.metrics import GATE, ConfusionMatrix


def test_perfect_prediction_scores_one():
    mask = np.array([[0, 1], [2, 2]], dtype=np.uint8)
    confusion = ConfusionMatrix()
    confusion.update(mask, mask)

    iou = confusion.iou_per_class()
    assert np.allclose(iou, 1.0)
    assert confusion.miou() == pytest.approx(1.0)
    assert confusion.pixel_accuracy() == pytest.approx(1.0)


def test_absent_class_is_nan_not_zero():
    """A class in neither truth nor prediction has undefined IoU.

    Scoring it 0.0 would drag mIoU down for a class the data never contained — the
    model cannot be penalised for a class it was never shown.
    """
    target = np.zeros((4, 4), dtype=np.uint8)
    confusion = ConfusionMatrix()
    confusion.update(target, target)

    iou = confusion.iou_per_class()
    assert iou[Class.STRUCTURE] == pytest.approx(1.0)
    assert np.isnan(iou[Class.WATER])
    assert np.isnan(iou[Class.WASTE])
    assert confusion.miou() == pytest.approx(1.0)  # nanmean over the one present class


def test_half_overlap_gives_expected_iou():
    # 4 waste pixels in truth, 4 predicted, 2 shared -> intersection 2, union 6.
    target = np.array([[2, 2, 2, 2], [0, 0, 0, 0]], dtype=np.uint8)
    pred = np.array([[2, 2, 0, 0], [2, 2, 0, 0]], dtype=np.uint8)

    confusion = ConfusionMatrix()
    confusion.update(target, pred)

    assert confusion.iou_per_class()[Class.WASTE] == pytest.approx(2 / 6)


def test_ignore_label_is_excluded():
    """255 marks unlabelled pixels and must not land in the matrix."""
    target = np.array([[2, 255], [255, 2]], dtype=np.uint8)
    pred = np.array([[2, 0], [1, 2]], dtype=np.uint8)

    confusion = ConfusionMatrix()
    confusion.update(target, pred)

    assert confusion.matrix.sum() == 2  # only the two labelled pixels
    assert confusion.iou_per_class()[Class.WASTE] == pytest.approx(1.0)


def test_updates_accumulate_across_batches():
    a = np.array([[2, 2]], dtype=np.uint8)
    b = np.array([[1, 1]], dtype=np.uint8)

    confusion = ConfusionMatrix()
    confusion.update(a, a)
    confusion.update(b, b)

    assert confusion.matrix.sum() == 4
    iou = confusion.iou_per_class()
    assert iou[Class.WASTE] == pytest.approx(1.0)
    assert iou[Class.WATER] == pytest.approx(1.0)


def test_shape_mismatch_is_rejected():
    confusion = ConfusionMatrix()
    with pytest.raises(ValueError, match="shape mismatch"):
        confusion.update(np.zeros((2, 2), np.uint8), np.zeros((3, 3), np.uint8))


def test_gate_fails_when_waste_iou_is_low():
    """The gate must key on waste/water, not on overall accuracy.

    This is the exact failure mode the gate exists to catch: a model that nails the
    majority class and misses the minority one still looks good on pixel accuracy.
    """
    # 100 structure pixels perfect; waste predicted as structure throughout.
    target = np.concatenate([np.zeros(100, np.uint8), np.full(10, 2, np.uint8)])
    pred = np.zeros(110, dtype=np.uint8)

    confusion = ConfusionMatrix()
    confusion.update(target, pred)

    assert confusion.pixel_accuracy() > 0.9  # deceptively high
    assert confusion.iou_per_class()[Class.WASTE] == pytest.approx(0.0)
    assert not confusion.meets_gate()


def test_gate_passes_when_both_classes_clear_thresholds():
    size = 100
    target = np.concatenate(
        [np.zeros(size, np.uint8), np.ones(size, np.uint8), np.full(size, 2, np.uint8)]
    )
    confusion = ConfusionMatrix()
    confusion.update(target, target)

    assert confusion.meets_gate()
    assert confusion.iou_per_class()[Class.WASTE] >= GATE[Class.WASTE]


def test_gate_fails_when_a_gated_class_is_absent():
    """An absent gated class cannot be declared passing.

    NaN is not >= a threshold, and treating "never seen" as "fine" would let an
    evaluation on a water-only split claim the waste gate had been met.
    """
    target = np.ones((4, 4), dtype=np.uint8)  # water only
    confusion = ConfusionMatrix()
    confusion.update(target, target)

    assert not confusion.meets_gate()


def test_report_mentions_gate_verdict_and_classes():
    target = np.array([[0, 1, 2]], dtype=np.uint8)
    confusion = ConfusionMatrix()
    confusion.update(target, target)

    report = confusion.report()
    for name in ("structure", "water", "waste", "mIoU", "gate"):
        assert name in report


def test_empty_matrix_does_not_divide_by_zero():
    confusion = ConfusionMatrix()
    assert np.isnan(confusion.pixel_accuracy())
    assert np.isnan(confusion.miou())
    assert not confusion.meets_gate()
