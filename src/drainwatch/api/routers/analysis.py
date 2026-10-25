"""Frame analysis route — the V1 interface's contract.

``POST /api/analyze`` is a thin adapter, not a second pipeline. It calls the same
:func:`drainwatch.api.scoring.score_image` the original ``POST /analyze`` calls, then
reshapes the result into the fields ``frontend/src/api/analyze.ts`` declares, and finally
persists an **inspection** so the detection enters the operational record.

The reshape is the whole job, and it is honest about the unit change:

    reading.reading_id          -> inspection_id   (a real inspection now, not a rename)
    choke_index      0–1        -> blockage_percentage 0–100
    severity  ok|watch|critical -> normal|warning|critical
    confidence       0–1        -> confidence 0–100
    image_url/overlay_url       -> evidence.original_url/segmentation_url
    waste_px/water_px/…         -> segmentation.classes[]

Doing this server-side rather than teaching the frontend the model's vocabulary means the
thresholds, the band mapping and the pixel arithmetic live in one place, and the UI keeps
rendering the three-band model it was designed around.
"""

from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image as PILImage

from drainwatch.api.scoring import class_breakdown, to_band
from drainwatch.ops import domain
from drainwatch.ops.security import get_current_user
from drainwatch.ops.store import EntityStore, get_ops_store, now_iso
from drainwatch.ops.serialize import inspection_out

router = APIRouter(prefix="/api", tags=["analysis"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # the V1 validates 10 MB; the server must too
MODEL_VERSION = "DrainSeg v1"


def _model():
    """The loaded model, or 503.

    Read from the app's ``STATE`` rather than loaded here, so the container's
    one-model-per-process behaviour is preserved — two model instances in one container
    is roughly double the memory for no benefit.
    """
    from drainwatch.api.main import STATE

    if STATE.get("model") is None or not STATE.get("model_trained", False):
        raise HTTPException(
            status_code=503,
            detail=STATE.get("error") or "The analysis model has not loaded a trained checkpoint.",
        )
    return STATE["model"]


def _detect_format(raw: bytes) -> str:
    """True image type from magic bytes.

    The declared content type is attacker-controlled and the frontend accepts by
    extension, so neither is trustworthy — a PNG named ``.jpg`` is fine, and a payload
    that is not an image at all should be rejected before it reaches the model.
    """
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


@router.post("/analyze")
async def analyze(
    image: UploadFile | None = File(default=None),
    source_id: str = Form(default="MANUAL"),
    asset_id: str = Form(default=""),
    captured_at: str | None = Form(default=None),
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Analyse a submitted frame and record the detection.

    A frame is required. The V1 can submit the source id alone to mean "pull the latest
    captured frame", which is the right production model — but there is no ingestion
    service behind this deployment, so there is no latest frame to pull. Rather than
    fabricate one, that case returns 400 explaining exactly that.
    """
    if image is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No frame was submitted. Camera ingestion is not connected in this "
                "deployment, so submit a drainage frame to analyse it."
            ),
        )

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=422, detail="The submitted frame was empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="The selected frame is too large. Choose an image under 10 MB.",
        )

    detected = _detect_format(raw)
    if not detected:
        raise HTTPException(
            status_code=422,
            detail="The selected file could not be decoded as a valid drainage image.",
        )

    try:
        frame = PILImage.open(io.BytesIO(raw))
        frame.load()
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a 422 to the client
        raise HTTPException(
            status_code=422,
            detail=f"The selected file could not be decoded as a valid drainage image. ({type(exc).__name__})",
        ) from exc

    source_id = (source_id or "MANUAL").strip()
    asset_id = (asset_id or "").strip()
    if not asset_id:
        raise HTTPException(
            status_code=400,
            detail="Associate the frame with a monitored asset before analysis.",
        )

    model = _model()

    from drainwatch.api.scoring import score_image

    reading, overlay, _mask = score_image(model, frame)
    data = reading.to_dict()
    choke = data.get("choke_index")
    block = float(choke) if choke is not None else 0.0
    band = to_band(str(data.get("severity", "invalid")), choke)

    # Evidence is kept so the inspection screen can show the frame and the segmentation.
    # A local key under the media mount for now; S3 when the AWS store is configured.
    evidence_key = uuid.uuid4().hex[:12]
    original_url = None
    segmentation_url = None
    try:
        storage = __import__("drainwatch.api.main", fromlist=["STATE"]).STATE.get("storage")
        if storage is not None:
            original_url = storage.put_image(f"analysis/{evidence_key}/original.jpg", raw, detected)
            buffer = io.BytesIO()
            overlay.save(buffer, format="PNG", optimize=True)
            segmentation_url = storage.put_image(
                f"analysis/{evidence_key}/segmentation.png", buffer.getvalue(), "image/png"
            )
    except Exception:  # noqa: BLE001 - evidence storage must not fail the detection
        original_url = None
        segmentation_url = None

    source = store.get("sources", source_id)
    if source:
        store.put(
            "sources",
            {
                **source,
                "last_frame": captured_at or now_iso(),
                "last_analysis": now_iso(),
                "health": "Current",
            },
        )

    confidence = float(data.get("confidence") or 0.0)
    inspection = domain.create_inspection(
        store,
        source_id=source_id,
        asset_id=asset_id,
        blockage=block,
        confidence=confidence,
        classes=class_breakdown(reading),
        model_version=MODEL_VERSION,
        captured_at=captured_at or now_iso(),
        original_url=original_url,
        segmentation_url=segmentation_url,
        submitted_by=user.get("name", user["id"]),
    )

    shaped = inspection_out(store, inspection)
    return {
        "inspection_id": shaped["id"],
        "source_id": shaped["source_id"],
        "asset_id": shaped["asset_id"],
        "captured_at": shaped["captured_at"],
        "blockage_percentage": shaped["blockage_percentage"],
        "severity": band,
        "confidence": shaped["confidence_percentage"],
        "model_version": MODEL_VERSION,
        "segmentation": {
            "image_url": segmentation_url,
            "mask_url": None,
            "classes": shaped["classes"],
        },
        "evidence": {
            "original_url": original_url,
            "segmentation_url": segmentation_url,
        },
        # The full record as well, so a caller can move straight to the inspection
        # without a second round trip.
        "inspection": shaped,
    }
