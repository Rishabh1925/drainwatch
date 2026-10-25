"""Tests for the Choke Index.

The degenerate cases are the point: a fully choked drain (no water at all) is exactly
where the naive ``waste / water`` ratio blows up, so it gets first-class coverage here.
"""

from __future__ import annotations

import numpy as np
import pytest

from drainwatch.choke.index import Severity, compute_choke_index, severity_from_index
from drainwatch.config import ChokeThresholds, Class


def mask_of(shape=(100, 100), fill=Class.STRUCTURE) -> np.ndarray:
    return np.full(shape, int(fill), dtype=np.uint8)


# --- degenerate / boundary cases ------------------------------------------------


def test_fully_blocked_drain_scores_one_and_does_not_divide_by_zero():
    """The case the naive waste/water ratio cannot express: zero water."""
    mask = mask_of(fill=Class.WASTE)

    reading = compute_choke_index(mask)

    assert reading.choke_index == pytest.approx(1.0)
    assert reading.severity is Severity.CRITICAL
    assert reading.water_px == 0
    assert reading.is_actionable


def test_fully_clear_drain_scores_zero():
    mask = mask_of(fill=Class.WATER)

    reading = compute_choke_index(mask)

    assert reading.choke_index == pytest.approx(0.0)
    assert reading.severity is Severity.OK
    assert not reading.is_actionable


def test_half_blocked_drain_scores_one_half():
    mask = mask_of(fill=Class.WATER)
    mask[:50, :] = Class.WASTE

    reading = compute_choke_index(mask)

    assert reading.choke_index == pytest.approx(0.5)
    assert reading.severity is Severity.WATCH


def test_frame_with_no_drain_channel_is_invalid_not_clear():
    """An all-structure frame must not read as "this drain is clear"."""
    mask = mask_of(fill=Class.STRUCTURE)

    reading = compute_choke_index(mask)

    assert reading.severity is Severity.INVALID
    assert reading.choke_index is None
    assert reading.channel_px == 0
    assert "no drain channel" in reading.reason


def test_channel_below_absolute_floor_is_invalid():
    """A handful of channel pixels is noise, not a reading."""
    mask = mask_of(fill=Class.STRUCTURE)
    mask[:10, :10] = Class.WASTE  # 100 px, under the 512 px floor

    reading = compute_choke_index(mask)

    assert reading.severity is Severity.INVALID
    assert reading.waste_px == 100


def test_min_channel_scales_with_frame_size():
    """A distant drain occupying 0.1% of a 4K frame is too small to trust,
    even though the same pixel count would be fine in a small frame."""
    big = np.full((2000, 2000), int(Class.STRUCTURE), dtype=np.uint8)
    big[:100, :100] = Class.WASTE  # 10k px = 0.25% of a 4M px frame

    assert compute_choke_index(big).severity is Severity.INVALID

    small = np.full((300, 300), int(Class.STRUCTURE), dtype=np.uint8)
    small[:100, :100] = Class.WASTE  # same 10k px, now 11% of the frame

    assert compute_choke_index(small).severity is Severity.CRITICAL


# --- severity thresholds --------------------------------------------------------


@pytest.mark.parametrize(
    ("ci", "expected"),
    [
        (0.00, Severity.OK),
        (0.39, Severity.OK),
        (0.399, Severity.OK),       # immediately below watch
        (0.3999, Severity.OK),      # sub-epsilon below watch
        (0.40, Severity.WATCH),     # inclusive lower bound
        (0.4001, Severity.WATCH),   # epsilon above watch
        (0.401, Severity.WATCH),
        (0.50, Severity.WATCH),
        (0.69, Severity.WATCH),
        (0.699, Severity.WATCH),    # immediately below critical
        (0.6999, Severity.WATCH),   # sub-epsilon below critical
        (0.70, Severity.CRITICAL),  # inclusive lower bound
        (0.7001, Severity.CRITICAL),  # epsilon above critical
        (0.701, Severity.CRITICAL),
        (1.00, Severity.CRITICAL),
    ],
)
def test_severity_bands(ci, expected):
    assert severity_from_index(ci) is expected


def test_thresholds_are_tunable():
    strict = ChokeThresholds(watch=0.1, critical=0.2)
    assert severity_from_index(0.25, strict) is Severity.CRITICAL
    assert severity_from_index(0.25) is Severity.OK  # default bands: watch starts at 0.40
    assert severity_from_index(0.45) is Severity.WATCH


# --- confidence gating ----------------------------------------------------------


def test_low_confidence_downgrades_to_needs_review_but_keeps_the_score():
    mask = mask_of(fill=Class.WASTE)
    confidence = np.full(mask.shape, 0.3, dtype=np.float32)

    reading = compute_choke_index(mask, confidence)

    assert reading.severity is Severity.NEEDS_REVIEW
    assert reading.choke_index == pytest.approx(1.0)  # operator still sees the number
    assert reading.confidence == pytest.approx(0.3)
    assert "confidence" in reading.reason


def test_high_confidence_passes_through():
    mask = mask_of(fill=Class.WASTE)
    confidence = np.full(mask.shape, 0.9, dtype=np.float32)

    reading = compute_choke_index(mask, confidence)

    assert reading.severity is Severity.CRITICAL
    assert reading.confidence == pytest.approx(0.9)


def test_confidence_is_averaged_over_channel_pixels_only():
    """Structure pixels must not dilute the channel confidence."""
    mask = mask_of(fill=Class.STRUCTURE)
    mask[:40, :] = Class.WASTE  # 4000 channel px
    confidence = np.full(mask.shape, 0.01, dtype=np.float32)  # structure: near zero
    confidence[:40, :] = 0.95  # channel: confident

    reading = compute_choke_index(mask, confidence)

    assert reading.confidence == pytest.approx(0.95)
    assert reading.severity is Severity.CRITICAL


# --- input validation -----------------------------------------------------------


def test_rejects_non_2d_mask():
    with pytest.raises(ValueError, match="2-D"):
        compute_choke_index(np.zeros((10, 10, 3), dtype=np.uint8))


def test_rejects_mismatched_confidence_shape():
    with pytest.raises(ValueError, match="does not match"):
        compute_choke_index(mask_of(), np.zeros((50, 50), dtype=np.float32))


# --- reporting ------------------------------------------------------------------


def test_to_dict_is_json_serializable():
    import json

    reading = compute_choke_index(mask_of(fill=Class.WASTE))
    payload = json.dumps(reading.to_dict())

    assert '"severity": "critical"' in payload
    assert '"choke_index": 1.0' in payload
