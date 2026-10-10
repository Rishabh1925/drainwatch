"""Choke Index — turns a per-pixel segmentation mask into an actionable blockage score.

Why not ``waste_px / water_px`` (the obvious formulation)?

A *fully* choked drain shows no water at all, so ``water_px -> 0`` and the ratio diverges
or divides by zero — it fails precisely in the case that matters most. It is also
projective: the same drain photographed from two angles yields two different ratios.

Instead we measure waste as a fraction of the drain *channel*, where the channel is
whatever the drain is filled with:

    channel_px = waste_px + water_px
    CI         = waste_px / channel_px        in [0, 1]

Clear drain -> 0.0. Fully blocked -> 1.0. Monotonic, bounded, no singularity, and
roughly stable across viewing angles for a fixed drain.

numpy-only by design: no torch import, so this is fast to test and cheap to import
in the API container. Morphological denoising lives in the inference path, not here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

import numpy as np

from drainwatch.config import DEFAULT_THRESHOLDS, Class, ChokeThresholds


class Severity(str, Enum):
    """Operational verdict. Drives pin colour and dispatch priority in the dashboard."""

    OK = "ok"
    WATCH = "watch"
    CRITICAL = "critical"
    NEEDS_REVIEW = "needs_review"  # scored, but the model was unsure — human should look
    INVALID = "invalid"  # no drain channel in frame; nothing to score


@dataclass(frozen=True)
class ChokeReading:
    """A single scored observation of a drain."""

    choke_index: float | None  # None only when severity is INVALID
    severity: Severity
    waste_px: int
    water_px: int
    channel_px: int
    total_px: int
    confidence: float | None  # mean model confidence over channel pixels
    reason: str | None = None  # why INVALID / NEEDS_REVIEW, for the operator

    @property
    def is_actionable(self) -> bool:
        """True when a crew should be dispatched or the drain watched."""
        return self.severity in (Severity.CRITICAL, Severity.WATCH)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


def severity_from_index(ci: float, thresholds: ChokeThresholds = DEFAULT_THRESHOLDS) -> Severity:
    """Map a Choke Index to a dispatch verdict. Pure, so the frontend can mirror it."""
    if ci >= thresholds.critical:
        return Severity.CRITICAL
    if ci >= thresholds.watch:
        return Severity.WATCH
    return Severity.OK


def compute_choke_index(
    mask: np.ndarray,
    confidence: np.ndarray | None = None,
    *,
    thresholds: ChokeThresholds = DEFAULT_THRESHOLDS,
) -> ChokeReading:
    """Score a segmentation mask.

    Args:
        mask: integer label array of shape (H, W) holding :class:`Class` values.
        confidence: optional per-pixel max-softmax probability, same shape as ``mask``.
            When given, a low mean confidence over the channel downgrades the verdict
            to ``NEEDS_REVIEW`` rather than silently reporting a confident-looking number.
        thresholds: tunables; defaults are calibrated on the Delhi validation set.

    Returns:
        A :class:`ChokeReading`. ``choke_index`` is ``None`` only when the frame
        contains no drain channel (``severity == INVALID``).

    Raises:
        ValueError: if ``mask`` is not 2-D, or ``confidence`` shape does not match.
    """
    if mask.ndim != 2:
        raise ValueError(f"mask must be 2-D (H, W), got shape {mask.shape}")
    if confidence is not None and confidence.shape != mask.shape:
        raise ValueError(
            f"confidence shape {confidence.shape} does not match mask shape {mask.shape}"
        )

    total_px = int(mask.size)
    waste_sel = mask == Class.WASTE
    water_sel = mask == Class.WATER
    waste_px = int(waste_sel.sum())
    water_px = int(water_sel.sum())
    channel_px = waste_px + water_px

    # No drain channel in frame — a wall, the sky, a misaimed camera. Refuse to score
    # rather than emit a meaningless 0.0 that would read as "this drain is clear".
    min_channel = thresholds.min_channel_px(total_px)
    if channel_px < min_channel:
        return ChokeReading(
            choke_index=None,
            severity=Severity.INVALID,
            waste_px=waste_px,
            water_px=water_px,
            channel_px=channel_px,
            total_px=total_px,
            confidence=None,
            reason=(
                f"no drain channel in frame: {channel_px} channel px "
                f"< {min_channel} required ({thresholds.min_channel_frac:.1%} of frame)"
            ),
        )

    ci = waste_px / channel_px

    channel_confidence: float | None = None
    if confidence is not None:
        channel_confidence = float(confidence[waste_sel | water_sel].mean())
        if channel_confidence < thresholds.min_confidence:
            return ChokeReading(
                choke_index=ci,
                severity=Severity.NEEDS_REVIEW,
                waste_px=waste_px,
                water_px=water_px,
                channel_px=channel_px,
                total_px=total_px,
                confidence=channel_confidence,
                reason=(
                    f"mean channel confidence {channel_confidence:.2f} "
                    f"< {thresholds.min_confidence:.2f}"
                ),
            )

    return ChokeReading(
        choke_index=ci,
        severity=severity_from_index(ci, thresholds),
        waste_px=waste_px,
        water_px=water_px,
        channel_px=channel_px,
        total_px=total_px,
        confidence=channel_confidence,
    )
