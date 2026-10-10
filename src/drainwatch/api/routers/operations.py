"""Operator routes: inspections, incidents, dispatch, crews, sources, assets, audit.

Every route requires a signed-in user (:func:`get_current_user`). Role is not checked
here beyond that — an operator legitimately does all of this. The admin-only surface is
in ``routers/admin.py``, and keeping the two apart is deliberate: "is this person signed
in" and "is this person an administrator" are different questions and mixing them into
one guard is how admin routes end up accidentally reachable.

State transitions live in ``ops.domain`` and are called, never reimplemented. The
verification gate on incident creation, the four-way state separation, and the
"completion does not close the incident" rule are all enforced there, so they hold for
any caller rather than only for this router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from drainwatch.ops import domain
from drainwatch.ops.security import get_current_user
from drainwatch.ops.serialize import (
    asset_out,
    audit_out,
    crew_out,
    incident_out,
    inspection_out,
    map_records,
    source_out,
    system_health,
    work_order_out,
)
from drainwatch.ops.store import EntityStore, get_ops_store

router = APIRouter(prefix="/api", tags=["operations"])


def _404(collection: str, record_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No {collection} record {record_id!r}.")


# --- inspections ------------------------------------------------------------------


@router.get("/inspections")
def list_inspections(
    verification: str | None = None,
    severity: str | None = None,
    asset_id: str | None = None,
    limit: int = 200,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Every inspection, newest first, optionally filtered."""
    records = store.all("inspections")
    if verification:
        records = [r for r in records if r.get("verification") == verification]
    if severity:
        records = [r for r in records if r.get("severity") == severity]
    if asset_id:
        records = [r for r in records if r.get("asset_id") == asset_id]
    records.sort(key=lambda r: r.get("captured_at", ""), reverse=True)
    return [inspection_out(store, r) for r in records[:limit]]


@router.get("/inspections/{inspection_id}")
def get_inspection(
    inspection_id: str,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    inspection = store.get("inspections", inspection_id)
    if not inspection:
        raise _404("inspection", inspection_id)
    return inspection_out(store, inspection)


@router.post("/inspections/{inspection_id}/verify")
def verify(
    inspection_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    if not store.get("inspections", inspection_id):
        raise _404("inspection", inspection_id)
    updated = domain.verify_inspection(
        store, inspection_id, actor=user.get("name", user["id"])
    )
    return inspection_out(store, updated)


@router.post("/inspections/{inspection_id}/reject")
def reject(
    payload: dict,
    inspection_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Mark as a false detection. Requires a reason — the audit is worthless without it."""
    if not store.get("inspections", inspection_id):
        raise _404("inspection", inspection_id)
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise HTTPException(
            status_code=422,
            detail="A reason is required when marking a detection as false.",
        )
    updated = domain.reject_inspection(
        store, inspection_id, actor=user.get("name", user["id"]), reason=reason
    )
    return inspection_out(store, updated)


@router.post("/inspections/{inspection_id}/override")
def override(
    payload: dict,
    inspection_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Change the effective severity, recording the reason.

    Validation is explicit here rather than trusting the client: an unknown severity
    string would silently become a colour the UI cannot render.
    """
    if not store.get("inspections", inspection_id):
        raise _404("inspection", inspection_id)
    severity = str(payload.get("severity", "")).lower()
    if severity not in ("normal", "warning", "critical"):
        raise HTTPException(
            status_code=422,
            detail="Severity must be one of: normal, warning, critical.",
        )
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A reason is required for an override.")
    updated = domain.override_severity(
        store, inspection_id, actor=user.get("name", user["id"]), severity=severity, reason=reason
    )
    return inspection_out(store, updated)


@router.post("/inspections/{inspection_id}/incident")
def create_incident_from_inspection(
    inspection_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Open an incident from a verified inspection.

    ``domain.create_incident`` raises ``PermissionError`` for an unverified inspection,
    which is translated to 409 rather than 400: the request is well-formed, the record
    is in the wrong state. The V1 disables the button; this is the rule behind it.
    """
    if not store.get("inspections", inspection_id):
        raise _404("inspection", inspection_id)
    try:
        incident = domain.create_incident(store, inspection_id, actor=user.get("name", user["id"]))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return incident_out(store, incident)


# --- incidents --------------------------------------------------------------------


@router.get("/incidents")
def list_incidents(
    state: str | None = None,
    active_only: bool = False,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    records = store.all("incidents")
    if state:
        records = [r for r in records if r.get("state") == state]
    if active_only:
        records = [r for r in records if r.get("state") not in domain.INCIDENT_TERMINAL]
    records.sort(key=lambda r: r.get("opened_at", ""), reverse=True)
    return [incident_out(store, r) for r in records]


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: str,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    incident = store.get("incidents", incident_id)
    if not incident:
        raise _404("incident", incident_id)
    shaped = incident_out(store, incident)
    shaped["audit"] = [audit_out(e) for e in domain.audit_for(store, incident_id)]
    return shaped


@router.post("/incidents/{incident_id}/state")
def set_incident_state(
    payload: dict,
    incident_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    if not store.get("incidents", incident_id):
        raise _404("incident", incident_id)
    state = str(payload.get("state", "")).strip()
    try:
        updated = domain.transition_incident(
            store,
            incident_id,
            state=state,
            actor=user.get("name", user["id"]),
            reason=str(payload.get("reason", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return incident_out(store, updated)


@router.post("/incidents/{incident_id}/close")
def close_incident(
    payload: dict,
    incident_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Close an incident after mitigation is verified.

    Deliberately a separate call from completing a work order. The V1 dispatch screen
    states that completing work does not close the incident; this is where that promise
    is kept.
    """
    if not store.get("incidents", incident_id):
        raise _404("incident", incident_id)
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise HTTPException(
            status_code=422, detail="A closure reason is required."
        )
    try:
        updated = domain.close_incident(
            store, incident_id, actor=user.get("name", user["id"]), reason=reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return incident_out(store, updated)


# --- work orders / dispatch -------------------------------------------------------


@router.get("/work-orders")
def list_work_orders(
    active_only: bool = False,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    records = store.all("work_orders")
    if active_only:
        records = [r for r in records if r.get("state") not in domain.WORK_ORDER_TERMINAL]
    records.sort(key=lambda r: r.get("dispatched_at", ""), reverse=True)
    return [work_order_out(store, r) for r in records]


@router.get("/work-orders/{work_order_id}")
def get_work_order(
    work_order_id: str,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    order = store.get("work_orders", work_order_id)
    if not order:
        raise _404("work order", work_order_id)
    shaped = work_order_out(store, order)
    shaped["audit"] = [audit_out(e) for e in domain.audit_for(store, work_order_id)]
    return shaped


@router.post("/incidents/{incident_id}/dispatch")
def dispatch(
    payload: dict,
    incident_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Assign a crew and raise a work order."""
    if not store.get("incidents", incident_id):
        raise _404("incident", incident_id)
    crew_id = str(payload.get("crew_id", "")).strip()
    if not crew_id:
        raise HTTPException(status_code=422, detail="A crew must be selected.")
    if not store.get("crews", crew_id):
        raise HTTPException(status_code=404, detail=f"No crew {crew_id!r}.")
    try:
        order = domain.create_work_order(
            store,
            incident_id,
            crew_id=crew_id,
            actor=user.get("name", user["id"]),
            instructions=str(payload.get("instructions", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return work_order_out(store, order)


@router.post("/work-orders/{work_order_id}/state")
def set_work_order_state(
    payload: dict,
    work_order_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    if not store.get("work_orders", work_order_id):
        raise _404("work order", work_order_id)
    state = str(payload.get("state", "")).strip()
    try:
        updated = domain.transition_work_order(
            store,
            work_order_id,
            state=state,
            actor=user.get("name", user["id"]),
            reason=str(payload.get("reason", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return work_order_out(store, updated)


@router.post("/work-orders/{work_order_id}/complete")
def complete(
    payload: dict,
    work_order_id: str,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Submit completed field work. Does **not** close the incident — see ``ops.domain``."""
    if not store.get("work_orders", work_order_id):
        raise _404("work order", work_order_id)
    note = str(payload.get("completion_note", "")).strip()
    if not note:
        raise HTTPException(status_code=422, detail="A completion note is required.")
    final = payload.get("final_blockage")
    final_fraction = None
    if final is not None:
        value = float(final)
        # Accept either a percentage or a fraction, since the field is labelled
        # "Final blockage / inspection" and 18.4 is the natural way to type it.
        final_fraction = value / 100 if value > 1 else value
    updated = domain.complete_work_order(
        store,
        work_order_id,
        actor=user.get("name", user["id"]),
        completion_note=note,
        final_blockage=final_fraction,
        evidence=payload.get("evidence"),
    )
    return work_order_out(store, updated)


# --- crews, sources, assets -------------------------------------------------------


@router.get("/crews")
def list_crews(
    _user: dict = Depends(get_current_user), store: EntityStore = Depends(get_ops_store)
):
    crews = sorted(store.all("crews"), key=lambda c: c.get("id", ""))
    return [crew_out(c) for c in crews]


@router.get("/sources")
def list_sources(
    _user: dict = Depends(get_current_user), store: EntityStore = Depends(get_ops_store)
):
    sources = sorted(store.all("sources"), key=lambda s: s.get("id", ""))
    return [source_out(s) for s in sources]


@router.get("/assets")
def list_assets(
    _user: dict = Depends(get_current_user), store: EntityStore = Depends(get_ops_store)
):
    assets = sorted(store.all("assets"), key=lambda a: a.get("id", ""))
    return [asset_out(store, a) for a in assets]


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """One asset with its detection history — the asset-history screen's data."""
    asset = store.get("assets", asset_id)
    if not asset:
        raise _404("asset", asset_id)
    shaped = asset_out(store, asset)
    history = store.find("inspections", lambda i: i.get("asset_id") == asset_id)
    history.sort(key=lambda i: i.get("captured_at", ""))
    shaped["history"] = [inspection_out(store, i) for i in history]
    return shaped


# --- audit / map / system ---------------------------------------------------------


@router.get("/audit")
def list_audit(
    record: str | None = None,
    actor: str | None = None,
    limit: int = 300,
    _user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """The append-only operational log, newest first."""
    events = store.all("audit_events")
    if record:
        events = [e for e in events if e.get("record") == record]
    if actor:
        events = [e for e in events if e.get("actor") == actor]
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return [audit_out(e) for e in events[:limit]]


@router.get("/map/records")
def get_map_records(
    _user: dict = Depends(get_current_user), store: EntityStore = Depends(get_ops_store)
):
    return map_records(store)


@router.get("/system/health")
def get_system_health(
    _user: dict = Depends(get_current_user), store: EntityStore = Depends(get_ops_store)
):
    """Service health derived from real records.

    The model's loaded state is read from the same ``STATE`` the analysis route uses, so
    this screen cannot claim the model is up while an upload would 503.
    """
    from drainwatch.api.main import STATE

    return system_health(
        store,
        model_loaded=STATE.get("model") is not None,
        device=str(STATE.get("device", "none")),
        storage=str(getattr(STATE.get("storage"), "name", "unknown")),
    )


@router.get("/overview")
def overview(
    _user: dict = Depends(get_current_user), store: EntityStore = Depends(get_ops_store)
):
    """The overview screen's figures, counted from records rather than asserted.

    Every number here is a count over stored data. The V1 mock had fixed figures
    ("3 critical incidents", "184 / 191 reporting"); those are replaced by real counts so
    an empty deployment honestly reads zero.
    """
    inspections = store.all("inspections")
    incidents = store.all("incidents")
    orders = store.all("work_orders")
    sources = store.all("sources")

    open_incidents = [i for i in incidents if i.get("state") not in domain.INCIDENT_TERMINAL]
    awaiting = [i for i in inspections if i.get("verification") == "Awaiting review"]
    unassigned = [i for i in incidents if i.get("state") == "Ready for dispatch"]
    active_orders = [o for o in orders if o.get("state") not in domain.WORK_ORDER_TERMINAL]
    reporting = [s for s in sources if s.get("status") == "Active"]

    critical = [
        incident_out(store, i)
        for i in sorted(open_incidents, key=lambda r: r.get("opened_at", ""), reverse=True)
    ]

    return {
        "counts": {
            "critical_incidents": len([i for i in open_incidents if i.get("severity") == "critical"]),
            "open_incidents": len(open_incidents),
            "awaiting_review": len(awaiting),
            "unassigned_dispatch": len(unassigned),
            "active_work_orders": len(active_orders),
            "sources": len(sources),
            "sources_reporting": len(reporting),
        },
        "priority_queue": critical[:10],
        "active_response": [work_order_out(store, o) for o in active_orders[:10]],
        "recent_inspections": [
            inspection_out(store, i)
            for i in sorted(inspections, key=lambda r: r.get("captured_at", ""), reverse=True)[:8]
        ],
    }
