"""Request/response models for the HTTP API.

These are the contract the React dashboard codes against, so the field names here are
the ones that appear in the demo. ``choke_index`` is deliberately ``float | None`` — an
INVALID reading must not report ``0.0``, which a dashboard would paint green.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from drainwatch.choke.index import Severity


class ReadingOut(BaseModel):
    """One scored observation of one drain at one time."""

    reading_id: str
    drain_id: str | None = None
    captured_at: str | None = Field(None, description="ISO-8601, from EXIF or upload time")

    choke_index: float | None = Field(
        None, description="waste / (waste + water), in [0, 1]. Null when INVALID."
    )
    severity: Severity
    reason: str | None = Field(None, description="Set when severity is INVALID or NEEDS_REVIEW")

    # Diagnostic detail, not the headline. Optional on purpose: a reading whose pixel
    # counts are missing is still a usable reading — it has a score and a severity — and
    # making these required means one malformed row in the table raises a validation
    # error that 500s the entire /drains response, blanking the whole dashboard rather
    # than one row of it. The product numbers above are what must be present.
    waste_px: int | None = None
    water_px: int | None = None
    channel_px: int | None = None
    total_px: int | None = None
    confidence: float | None = Field(None, description="Mean softmax over channel pixels")

    lat: float | None = None
    lon: float | None = None
    site_name: str | None = Field(
        None, description="Human-readable place name, when one is known for the location"
    )

    image_url: str | None = None
    overlay_url: str | None = None

    simulated: bool = Field(
        False,
        description=(
            "True for seeded demo readings. Surfaced in the API and badged in the UI so a "
            "synthetic history can never be mistaken for a measurement."
        ),
    )


class DrainOut(BaseModel):
    """A drain's current state plus enough history to draw a sparkline.

    ``trend`` is the headline number: a drain at 0.5 and falling is a lower priority
    than a drain at 0.5 and climbing, and that distinction is the whole pitch for
    predictive rather than reactive maintenance.
    """

    drain_id: str
    lat: float | None = None
    lon: float | None = None
    site_name: str | None = None
    latest: ReadingOut
    reading_count: int
    trend: float | None = Field(
        None, description="Change in choke index from the first to the latest reading"
    )
    history: list[float | None] = Field(default_factory=list)
    simulated: bool = Field(
        False, description="True if any reading in this drain's history was seeded, not measured"
    )


class AnalyzeResponse(BaseModel):
    reading: ReadingOut
    drain: DrainOut | None = Field(
        None, description="Null when the image carried no location and none was supplied"
    )


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    device: str
    storage: str
    thresholds: dict
