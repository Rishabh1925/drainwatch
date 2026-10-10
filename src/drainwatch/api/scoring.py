"""The image → reading → overlay scoring step, in one place.

Both analysis routes call this: ``POST /analyze`` (the original contract, kept working
for the CLI and the existing tests) and ``POST /api/analyze`` (the V1 interface's
contract). The model invocation, the Choke Index computation and the overlay rendering
are identical between them, so they share an implementation rather than drifting apart —
a second copy of the scoring path is exactly the kind of duplicate system that ends up
disagreeing with the first.
"""

from __future__ import annotations

from PIL import Image as PILImage

from drainwatch.choke.index import ChokeReading, compute_choke_index
from drainwatch.choke.overlay import render_overlay
from drainwatch.model import segformer


def score_image(
    model, image: PILImage.Image
) -> tuple[ChokeReading, PILImage.Image, "object"]:
    """Segment an image and score it.

    Returns ``(reading, overlay, mask)``. The mask is handed back as well because the
    V1 interface renders per-class pixel counts, and recomputing the mask to get them
    would double the inference cost for no reason.

    ``predict`` is called through the module (``segformer.predict``) rather than a bound
    import, so a test that patches ``drainwatch.model.segformer.predict`` is honoured —
    binding it at import time would freeze the real implementation in place.
    """
    mask, confidence = segformer.predict(model, image)
    reading = compute_choke_index(mask, confidence)
    overlay = render_overlay(image, mask)
    return reading, overlay, mask


def class_breakdown(reading: ChokeReading) -> list[dict]:
    """Per-class pixel counts in the vocabulary the V1 interface renders.

    "structural" rather than "structure": the frontend's legend and class bars use that
    spelling, and its ``result.name.replace("structural", ...)`` label depends on it.

    Structure is derived as ``total - channel`` rather than carried on the reading. The
    Choke Index only ever needed waste and water, so those are what it counts; the
    remainder is by definition everything else in the frame.
    """
    data = reading.to_dict()
    total = int(data.get("total_px") or 0)
    waste = int(data.get("waste_px") or 0)
    water = int(data.get("water_px") or 0)
    channel = int(data.get("channel_px") or (waste + water))
    structure = max(0, total - channel)

    def entry(name: str, count: int) -> dict:
        return {
            "name": name,
            "pixel_count": count,
            "percentage": round(count / total * 100, 2) if total else 0.0,
        }

    return [entry("structural", structure), entry("water", water), entry("waste", waste)]


# The V1 severity vocabulary is three bands. The model emits five values, and two of them
# are not bands at all — they are confidence verdicts:
#   needs_review -> the model scored it but is unsure; keep the band, flag the doubt
#   invalid      -> no drain channel in frame; there is no band to report
SEVERITY_TO_BAND = {
    "ok": "normal",
    "watch": "warning",
    "critical": "critical",
}


def to_band(severity: str, choke_index: float | None) -> str:
    """Map the model's severity vocabulary onto the V1's three bands.

    ``needs_review`` is banded by its index rather than discarded, because a low-
    confidence reading is still the best evidence available and the interface has a
    separate confidence field to express the doubt in.
    """
    if severity in SEVERITY_TO_BAND:
        return SEVERITY_TO_BAND[severity]
    if severity in ("needs_review", "invalid") and choke_index is not None:
        if choke_index >= 0.70:
            return "critical"
        if choke_index >= 0.40:
            return "warning"
        return "normal"
    return "normal"
