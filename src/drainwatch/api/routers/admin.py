"""Administration routes: authorized users and monitoring sources.

Every route here sits behind :func:`require_admin`. The V1 hides the admin navigation from
operators as a UI affordance; this is the boundary that actually holds. An operator who
reaches ``/api/admin/users`` directly gets 403, and the frontend's ``Administration.tsx``
already renders that as an access-denied state.

Source coordinates come from the map picker or geolocation on the client, which is
correct for *infrastructure* location — a camera's position is a property of the camera,
not of the operator standing next to it. The distinction is preserved here: this endpoint
stores what the caller passes and never infers a location.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from drainwatch.ops.domain import record_event
from drainwatch.ops.security import public_user, require_admin
from drainwatch.ops.store import EntityStore, get_ops_store, now_iso

router = APIRouter(prefix="/api/admin", tags=["admin"])

SOURCE_TYPES = ("CCTV", "Drone", "Inspection camera", "Citizen report")


def _admin_user(user: dict) -> dict:
    """The subset ``AdminUser`` declares — no credentials, no profile preferences."""
    return {
        "id": user["id"],
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", "OPERATOR"),
        "status": user.get("status", "Active"),
        "last_sign_in": user.get("last_sign_in"),
    }


def _source(source: dict) -> dict:
    return {
        "id": source["id"],
        "type": source.get("type", "CCTV"),
        "status": source.get("status", "Active"),
        "coverage": source.get("coverage", ""),
        "last_frame": source.get("last_frame"),
        "last_analysis": source.get("last_analysis"),
        "latitude": source.get("latitude"),
        "longitude": source.get("longitude"),
        "sector": source.get("sector"),
        "health": source.get("health", "Current"),
    }


@router.get("/users")
def list_users(
    _admin: dict = Depends(require_admin), store: EntityStore = Depends(get_ops_store)
):
    users = sorted(store.all("users"), key=lambda u: u.get("name", ""))
    return [_admin_user(user) for user in users]


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    payload: dict,
    admin: dict = Depends(require_admin),
    store: EntityStore = Depends(get_ops_store),
):
    user = store.get("users", user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"No user {user_id!r}.")

    if "enabled" in payload:
        if user["id"] == admin["id"]:
            # Refusing self-disable is not paternalism: the only route back in is another
            # admin, and a single-admin deployment would lock itself out permanently.
            raise HTTPException(
                status_code=409, detail="You cannot disable your own account."
            )
        status = "Active" if payload["enabled"] else "Disabled"
        updated = store.put("users", {**user, "status": status})
        record_event(
            store,
            actor=admin.get("name", admin["id"]),
            kind="Account",
            record_id=user_id,
            action=f"Account {status.lower()}",
            previous=user.get("status", "Active"),
            next_state=status,
            reason="Administrative account control.",
        )
        return _admin_user(updated)

    allowed = {"name", "role", "title", "organization", "jurisdiction"}
    changes = {k: v for k, v in payload.items() if k in allowed}
    if not changes:
        raise HTTPException(status_code=422, detail="No editable fields were provided.")
    updated = store.put("users", {**user, **changes})
    record_event(
        store,
        actor=admin.get("name", admin["id"]),
        kind="Account",
        record_id=user_id,
        action="Account updated",
        reason=f"Changed: {', '.join(sorted(changes))}.",
    )
    return _admin_user(updated)


@router.get("/sources")
def list_sources(
    _admin: dict = Depends(require_admin), store: EntityStore = Depends(get_ops_store)
):
    sources = sorted(store.all("sources"), key=lambda s: s.get("id", ""))
    return [_source(source) for source in sources]


@router.post("/sources")
def create_source(
    payload: dict,
    admin: dict = Depends(require_admin),
    store: EntityStore = Depends(get_ops_store),
):
    source_id = str(payload.get("id", "")).strip()
    if not source_id:
        raise HTTPException(status_code=422, detail="A source ID is required.")
    if store.get("sources", source_id):
        raise HTTPException(status_code=409, detail=f"Source {source_id} already exists.")

    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if latitude is None or longitude is None:
        raise HTTPException(
            status_code=422,
            detail="A monitoring source needs a location before it can be registered.",
        )

    source = store.put(
        "sources",
        {
            "id": source_id,
            "type": payload.get("type", "CCTV"),
            "status": "Active",
            "coverage": payload.get("coverage", ""),
            "sector": payload.get("sector"),
            "latitude": float(latitude),
            "longitude": float(longitude),
            "last_frame": None,
            "last_analysis": None,
            "health": "Current",
        },
    )
    record_event(
        store,
        actor=admin.get("name", admin["id"]),
        kind="Source",
        record_id=source_id,
        action="Source registered",
        previous="—",
        next_state="Active",
        reason=f"{source['type']} covering {source['coverage']}.",
    )
    return _source(source)


@router.patch("/sources/{source_id}")
def update_source(
    source_id: str,
    payload: dict,
    admin: dict = Depends(require_admin),
    store: EntityStore = Depends(get_ops_store),
):
    source = store.get("sources", source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"No source {source_id!r}.")

    changes: dict = {}
    if "enabled" in payload:
        changes["status"] = "Active" if payload["enabled"] else "Disabled"
    for field in ("type", "coverage", "sector", "health", "latitude", "longitude"):
        if field in payload:
            changes[field] = payload[field]

    if not changes:
        raise HTTPException(status_code=422, detail="No editable fields were provided.")

    updated = store.put("sources", {**source, **changes})
    record_event(
        store,
        actor=admin.get("name", admin["id"]),
        kind="Source",
        record_id=source_id,
        action="Source updated",
        previous=source.get("status", ""),
        next_state=updated.get("status", ""),
        reason="Administrative source control.",
    )
    return _source(updated)


@router.get("/access-requests")
def list_access_requests(
    _admin: dict = Depends(require_admin), store: EntityStore = Depends(get_ops_store)
):
    """Pending password-reset and access requests, newest first.

    Surfaced because the auth endpoints record them and nothing else reads them — a
    queue nobody can see is the same as no queue.
    """
    requests = sorted(
        store.all("access_requests"), key=lambda r: r.get("requested_at", ""), reverse=True
    )
    return [r for r in requests if not r.get("handled")]
