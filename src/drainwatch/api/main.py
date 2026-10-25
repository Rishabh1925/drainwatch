"""FastAPI service: analyze an image, serve the dashboard.

One container serves both the API and the built React app, so there is a single deploy
target on ECS Fargate instead of two.

**The model requires a trained checkpoint to serve predictions.** A missing or invalid
checkpoint does not crash the container — it boots, serves ``/health``, and reports the
error — but ``/analyze`` returns 503 rather than producing predictions from an untrained
segmentation head. ``/health`` reports ``model_loaded``, ``model_trained``, and
``checkpoint`` so the operational state is unambiguous.
"""

from __future__ import annotations

# Load .env before anything else reads os.environ
from dotenv import load_dotenv
load_dotenv()

import io
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image

from drainwatch.api.routers import admin as admin_router
from drainwatch.api.routers import analysis as analysis_router
from drainwatch.api.routers import auth as auth_router
from drainwatch.api.routers import operations as operations_router
from drainwatch.api.schemas import AnalyzeResponse, DrainOut, HealthResponse, ReadingOut
from drainwatch.api.storage import (
    LocalStorage,
    get_storage,
    group_readings,
    trend_of,
)
from drainwatch.choke.index import DEFAULT_THRESHOLDS
from drainwatch.config import CHECKPOINT_DIR, PROJECT_ROOT

# The V1 interface lives in frontend/ and is what gets served. web/ was an earlier
# build superseded by it; it is checked second only so an old local build does not
# shadow the current one.
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
WEB_DIST = PROJECT_ROOT / "web" / "dist"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Populated in the lifespan handler.
STATE: dict = {"model": None, "device": "none", "storage": None, "error": None, "model_trained": False, "checkpoint": "none"}


def _load_model_quietly() -> None:
    """Load the trained checkpoint. Refuse to silently serve an untrained head.

    A missing checkpoint means the model is not ready for inference — STATE['model']
    stays None and ``/analyze`` returns 503. The health endpoint reports the distinction
    so operators can see exactly what happened.

    The pretrained base with a freshly initialised 3-class head is **never** loaded for
    production inference: its predictions are meaningless for drainage blockage scoring,
    and presenting them as valid readings would be dishonest. If you need the base model
    for training or development, use ``load_model()`` directly with no checkpoint arg.
    """
    try:
        from drainwatch.model.segformer import load_model, pick_device

        checkpoint = Path(os.environ.get("DRAINWATCH_CHECKPOINT", CHECKPOINT_DIR / "best"))
        device = pick_device(os.environ.get("DRAINWATCH_DEVICE"))

        if not checkpoint.exists():
            STATE["error"] = (
                f"trained checkpoint not found at {checkpoint}. "
                "The model cannot serve predictions without a trained checkpoint. "
                "See README for checkpoint setup instructions."
            )
            STATE["checkpoint"] = "missing"
            print(f"model load skipped: {STATE['error']}")
            return

        # Validate that the checkpoint contains the expected model configuration.
        config_path = checkpoint / "config.json"
        if not config_path.exists():
            STATE["error"] = (
                f"checkpoint directory {checkpoint} exists but has no config.json. "
                "This may be a corrupted or incomplete checkpoint."
            )
            STATE["checkpoint"] = "invalid"
            print(f"model load failed: {STATE['error']}")
            return

        # Validate checkpoint compatibility: num_labels must match our class count.
        import json as _json
        try:
            ckpt_config = _json.loads(config_path.read_text())
            ckpt_num_labels = ckpt_config.get("num_labels", ckpt_config.get("num_classes"))
            from drainwatch.config import NUM_CLASSES
            if ckpt_num_labels is not None and int(ckpt_num_labels) != NUM_CLASSES:
                STATE["error"] = (
                    f"checkpoint has {ckpt_num_labels} classes but the application "
                    f"expects {NUM_CLASSES}. The checkpoint is incompatible."
                )
                STATE["checkpoint"] = "incompatible"
                print(f"model load failed: {STATE['error']}")
                return
        except Exception as exc:
            print(f"warning: could not validate checkpoint config: {exc}")

        STATE["model"] = load_model(checkpoint)
        STATE["device"] = str(device)
        STATE["checkpoint"] = str(checkpoint)
        STATE["model_trained"] = True
    except Exception as exc:  # noqa: BLE001 - deliberate: boot without a model
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        STATE["checkpoint"] = "error"
        print(f"model load failed, serving without inference: {STATE['error']}")



@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["storage"] = get_storage()
    _load_model_quietly()
    yield


app = FastAPI(
    title="DrainWatch",
    description="Open drain choke-point detection from street-level imagery.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for the V1 frontend during development. The Vite dev server runs on 8443 (the
# port frontend/vite.config.ts pins) and 5173 (Vite's default, in case it is changed).
# No wildcard: a wildcard origin cannot be combined with credentialed requests, and the
# session cookie is exactly that — so the allowed origins are listed explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8443",
        "http://127.0.0.1:8443",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Operations API. Included before the static mounts at the bottom of this module, which
# is what keeps the SPA catch-all from shadowing these routes.
app.include_router(auth_router.router)
app.include_router(operations_router.router)
app.include_router(admin_router.router)
app.include_router(analysis_router.router)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _drain_from_history(drain_id: str, history: list[dict]) -> DrainOut:
    latest = history[-1]
    return DrainOut(
        drain_id=drain_id,
        lat=latest.get("lat"),
        lon=latest.get("lon"),
        site_name=latest.get("site_name"),
        latest=ReadingOut(**{k: v for k, v in latest.items() if k in ReadingOut.model_fields}),
        reading_count=len(history),
        trend=trend_of(history),
        history=[r.get("choke_index") for r in history],
        # One seeded reading taints the whole series: the sparkline and the trend number
        # are computed across it, so the badge belongs on the drain, not just the reading.
        simulated=any(r.get("simulated") for r in history),
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    storage = STATE["storage"]
    return HealthResponse(
        status="ok",
        model_loaded=STATE["model"] is not None,
        model_trained=STATE.get("model_trained", False),
        checkpoint=STATE.get("checkpoint", "none"),
        device=STATE["device"],
        storage=getattr(storage, "name", "none"),
        thresholds=asdict(DEFAULT_THRESHOLDS),
    )


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    file: UploadFile = File(...),
    lat: float | None = Form(None),
    lon: float | None = Form(None),
    captured_at: str | None = Form(None),
) -> AnalyzeResponse:
    """Segment one image, score it, store it, and return the reading.

    Coordinates are taken from the form when supplied, else from EXIF. Most web-sourced
    images have EXIF stripped, so the form override is the normal path for a demo, not a
    workaround.
    """
    if STATE["model"] is None or not STATE.get("model_trained", False):
        raise HTTPException(503, f"model unavailable: {STATE['error'] or 'no trained checkpoint loaded'}")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"image larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"not a readable image: {exc}") from exc

    from drainwatch.api.scoring import score_image
    from drainwatch.geo.sources import GeoPoint, extract_captured_at, extract_gps

    point: GeoPoint | None = None
    if lat is not None and lon is not None:
        try:
            point = GeoPoint(lat, lon)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        point = extract_gps(image)

    when = captured_at or extract_captured_at(image) or _now_iso()

    # Shared with POST /api/analyze — one scoring path, two response contracts.
    reading, overlay, _mask = score_image(STATE["model"], image)

    reading_id = uuid.uuid4().hex[:12]
    storage = STATE["storage"]
    prefix = f"readings/{reading_id}"
    image_url = storage.put_image(f"{prefix}/source.jpg", raw, file.content_type or "image/jpeg")
    overlay_url = storage.put_image(f"{prefix}/overlay.png", _png_bytes(overlay), "image/png")

    record = {
        "reading_id": reading_id,
        "drain_id": point.drain_id if point else None,
        "captured_at": when,
        "lat": point.lat if point else None,
        "lon": point.lon if point else None,
        "image_url": image_url,
        "overlay_url": overlay_url,
        **reading.to_dict(),
    }
    storage.put_reading(record)

    drain = None
    if point:
        history = storage.drain_history(point.drain_id)
        # A fresh AWS write may not be read back immediately; fall back to this reading.
        drain = _drain_from_history(point.drain_id, history or [record])

    return AnalyzeResponse(
        reading=ReadingOut(**{k: v for k, v in record.items() if k in ReadingOut.model_fields}),
        drain=drain,
    )


@app.get("/drains", response_model=list[DrainOut])
def list_drains(sort: str = "priority") -> list[DrainOut]:
    """All drains with their latest reading.

    Default ordering is the priority queue the dashboard leads with: worst first, and
    among similar scores the ones that are *climbing* ahead of the ones that are not.

    A drain whose records cannot be parsed is skipped rather than allowed to fail the
    whole response. This is the list the dashboard's map and queue are both built from,
    so one malformed row turning into a 500 would blank the entire UI — the worst
    possible trade during a demo. The skip is logged, never hidden.
    """
    grouped = group_readings(STATE["storage"].all_readings())

    drains: list[DrainOut] = []
    for drain_id, history in grouped.items():
        try:
            drains.append(_drain_from_history(drain_id, history))
        except Exception as exc:  # noqa: BLE001 - one bad drain must not blank the map
            print(f"skipping drain {drain_id}: {type(exc).__name__}: {exc}")

    if sort == "priority":
        drains.sort(
            key=lambda d: (
                d.latest.choke_index if d.latest.choke_index is not None else -1.0,
                d.trend or 0.0,
            ),
            reverse=True,
        )
    elif sort == "trend":
        drains.sort(key=lambda d: d.trend if d.trend is not None else -99.0, reverse=True)
    return drains


@app.get("/drains/{drain_id}", response_model=DrainOut)
def get_drain(drain_id: str) -> DrainOut:
    history = STATE["storage"].drain_history(drain_id)
    if not history:
        raise HTTPException(404, f"no readings for drain {drain_id}")
    return _drain_from_history(drain_id, history)


@app.get("/drains/{drain_id}/history", response_model=list[ReadingOut])
def drain_history(drain_id: str) -> list[ReadingOut]:
    history = STATE["storage"].drain_history(drain_id)
    if not history:
        raise HTTPException(404, f"no readings for drain {drain_id}")
    return [
        ReadingOut(**{k: v for k, v in r.items() if k in ReadingOut.model_fields}) for r in history
    ]


# Static mounts last: a catch-all at "/" would otherwise shadow the API routes.
_local = LocalStorage()
if _local.media.is_dir():
    app.mount("/media", StaticFiles(directory=_local.media), name="media")

# Serve the built V1 frontend. web/ is the retired earlier build and is only used if the
# V1 has not been built yet, so a stale web/dist can never shadow a built frontend/dist.
_spa_dist = FRONTEND_DIST if FRONTEND_DIST.is_dir() else WEB_DIST if WEB_DIST.is_dir() else None
if _spa_dist is not None:
    app.mount("/", StaticFiles(directory=_spa_dist, html=True), name="web")
