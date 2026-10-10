"""Operational domain: records, their state machines, and the audit trail.

This is where the distinctions the V1 interface draws are enforced rather than merely
displayed. Four independent states are tracked for one blocked drain, and collapsing any
two of them would be the bug that matters most:

    inspection.verification   model said X, a human agreed or disagreed
    incident.state            an operational response is owed
    work_order.state          a crew has been sent and is working
    incident.closure          the problem is confirmed resolved

A completed work order does **not** close its incident. The crew finishing is a claim;
closing the incident is a separate verification of the mitigation. That separation is
stated in the V1 dispatch screen ("Completing this work order does not close the
incident") and it is enforced here — :func:`complete_work_order` deliberately never
touches the incident.

Every mutation writes an audit event. The audit log is append-only in the sense that
records are never edited in place: a correction is a new event carrying ``previous`` and
``next``, so the history of a record can be replayed rather than trusted.
"""

from __future__ import annotations

from typing import Any

from drainwatch.ops.store import EntityStore, now_iso, slugify

# --- vocabularies -----------------------------------------------------------------

VERIFICATION_STATES = ("Awaiting review", "Verified", "False detection", "Referred")
INCIDENT_STATES = (
    "New",
    "Acknowledged",
    "Verified",
    "Ready for dispatch",
    "Dispatched",
    "On site",
    "Mitigated",
    "Closed",
    "Dismissed",
)
WORK_ORDER_STATES = (
    "Assigned",
    "Dispatched",
    "Acknowledged",
    "En route",
    "On site",
    "Completed",
    "Cancelled",
)

# Terminal states — a record here accepts no further transition.
INCIDENT_TERMINAL = {"Closed", "Dismissed"}
WORK_ORDER_TERMINAL = {"Completed", "Cancelled"}


def severity_from_blockage(blockage: float) -> str:
    """Map a 0–1 blockage fraction to the three-band vocabulary the V1 renders.

    The thresholds are the product's fixed decision boundaries (0.40 watch, 0.70
    dispatch) and are defined once, in ``config.DEFAULT_THRESHOLDS``, then mirrored here
    for the three-band UI vocabulary. The frontend never computes severity — it renders
    what this returns — so there is one authority for the number.
    """
    if blockage >= 0.70:
        return "critical"
    if blockage >= 0.40:
        return "warning"
    return "normal"


# --- audit ------------------------------------------------------------------------


def record_event(
    store: EntityStore,
    *,
    actor: str,
    kind: str,
    record_id: str,
    action: str,
    previous: str = "—",
    next_state: str = "—",
    reason: str = "",
    outcome: str = "success",
) -> dict:
    """Append one audit event.

    ``actor`` is a display name or a process name ("DrainSeg v1", "CCTV ingestion"),
    never a user id — the History screen shows it verbatim and a slug would read badly.
    """
    event_id = store.next_id("audit_events", "EVT-", width=6, start=938_400)
    return store.put(
        "audit_events",
        {
            "id": event_id,
            "timestamp": now_iso(),
            "actor": actor,
            "type": kind,
            "record": record_id,
            "action": action,
            "previous": previous,
            "next": next_state,
            "reason": reason,
            "outcome": outcome,
        },
    )


def audit_for(store: EntityStore, record_id: str) -> list[dict]:
    """A record's audit trail, oldest first."""
    events = store.find("audit_events", lambda e: e.get("record") == record_id)
    return sorted(events, key=lambda e: e.get("timestamp", ""))


# --- inspections ------------------------------------------------------------------


def create_inspection(
    store: EntityStore,
    *,
    source_id: str,
    asset_id: str,
    blockage: float,
    confidence: float,
    classes: list[dict] | None = None,
    model_version: str = "DrainSeg v1",
    captured_at: str | None = None,
    original_url: str | None = None,
    segmentation_url: str | None = None,
    submitted_by: str | None = None,
    previous_blockage: float | None = None,
) -> dict:
    """Persist a model result as an inspection awaiting human verification.

    An inspection is created *only* by the model path or the seeder — never by a client
    asserting a blockage — so a detection always traces to something the system actually
    computed.
    """
    inspection_id = store.next_id("inspections", "INSP-", width=5, start=24_880)
    block = round(float(blockage), 4)
    record = {
        "id": inspection_id,
        "source_id": source_id,
        "asset_id": asset_id,
        "captured_at": captured_at or now_iso(),
        "blockage": block,
        "severity": severity_from_blockage(block),
        "confidence": round(float(confidence), 4),
        "classes": classes or [],
        "model_version": model_version,
        "verification": "Awaiting review",
        "reviewer": None,
        "verified_at": None,
        "previous_blockage": previous_blockage,
        "original_url": original_url,
        "segmentation_url": segmentation_url,
        "submitted_by": submitted_by,
        "incident_id": None,
        "rejection_reason": None,
        "override": None,
    }
    store.put("inspections", record)
    record_event(
        store,
        actor=model_version,
        kind="Detection",
        record_id=inspection_id,
        action="Analysis completed",
        previous="Processing",
        next_state="Awaiting review",
        reason=f"{severity_from_blockage(block).capitalize()} blockage calculated at "
        f"{block * 100:.1f}%.",
    )
    return record


def verify_inspection(store: EntityStore, inspection_id: str, *, actor: str) -> dict:
    """Operator confirms a detection. This is the gate before an incident can exist."""
    inspection = _require(store, "inspections", inspection_id)
    if inspection.get("verification") == "Verified":
        return inspection
    updated = store.put(
        "inspections",
        {**inspection, "verification": "Verified", "reviewer": actor, "verified_at": now_iso()},
    )
    record_event(
        store,
        actor=actor,
        kind="Inspection",
        record_id=inspection_id,
        action="Verified detection",
        previous=inspection.get("verification", "Awaiting review"),
        next_state="Verified",
        reason="Segmentation and source image confirmed as valid.",
    )
    return updated


def reject_inspection(
    store: EntityStore, inspection_id: str, *, actor: str, reason: str
) -> dict:
    """Mark a detection as a false positive. Kept, never deleted — the audit needs it."""
    inspection = _require(store, "inspections", inspection_id)
    updated = store.put(
        "inspections",
        {
            **inspection,
            "verification": "False detection",
            "reviewer": actor,
            "verified_at": now_iso(),
            "rejection_reason": reason,
        },
    )
    record_event(
        store,
        actor=actor,
        kind="Inspection",
        record_id=inspection_id,
        action="Marked as false detection",
        previous=inspection.get("verification", "Awaiting review"),
        next_state="False detection",
        reason=reason or "Model output does not represent a functional-channel blockage.",
    )
    return updated


def override_severity(
    store: EntityStore, inspection_id: str, *, actor: str, severity: str, reason: str
) -> dict:
    """Change the operator-confirmed severity, recording why.

    The model-computed severity is deliberately left untouched — the V1 shows both side
    by side, and overwriting the model's output would destroy the evidence that a human
    disagreed with it.
    """
    inspection = _require(store, "inspections", inspection_id)
    updated = store.put(
        "inspections",
        {
            **inspection,
            "override": {
                "severity": severity,
                "reason": reason,
                "actor": actor,
                "at": now_iso(),
                "model_severity": inspection.get("severity"),
            },
        },
    )
    record_event(
        store,
        actor=actor,
        kind="Inspection",
        record_id=inspection_id,
        action="Severity overridden",
        previous=inspection.get("severity", "").capitalize(),
        next_state=severity.capitalize(),
        reason=reason,
    )
    return updated


def effective_severity(inspection: dict) -> str:
    """Operator override if present, else the model's computation."""
    override = inspection.get("override") or {}
    return override.get("severity") or inspection.get("severity", "normal")


# --- incidents --------------------------------------------------------------------


def create_incident(
    store: EntityStore, inspection_id: str, *, actor: str
) -> dict:
    """Open an incident from a **verified** inspection.

    Refuses an unverified inspection: the V1 disables the Create-incident control until
    verification, and this is the same rule enforced where it cannot be bypassed.
    """
    inspection = _require(store, "inspections", inspection_id)
    if inspection.get("verification") != "Verified":
        raise PermissionError(
            "An incident can only be created from a verified inspection. "
            f"{inspection_id} is {inspection.get('verification', 'Awaiting review')}."
        )
    if inspection.get("incident_id"):
        return _require(store, "incidents", inspection["incident_id"])

    incident_id = store.next_id("incidents", "INC-", width=5, start=24_880)
    severity = effective_severity(inspection)
    incident = store.put(
        "incidents",
        {
            "id": incident_id,
            "inspection_id": inspection_id,
            "asset_id": inspection["asset_id"],
            "source_id": inspection["source_id"],
            "blockage": inspection.get("blockage"),
            "severity": severity,
            # Born "Ready for dispatch": an incident only exists once its inspection is
            # verified, so the verification step of the lifecycle is already complete.
            # Starting at "New" would force an operator to click through states that the
            # verification gate already satisfied.
            "state": "Ready for dispatch",
            "owner": None,
            "work_order_id": None,
            "opened_at": now_iso(),
            "closed_at": None,
            "closure_reason": None,
            "sla_target_minutes": 90,
            "notes": [],
        },
    )
    store.put("inspections", {**inspection, "incident_id": incident_id})
    record_event(
        store,
        actor=actor,
        kind="Incident",
        record_id=incident_id,
        action="Incident created",
        previous="—",
        next_state="Ready for dispatch",
        reason=f"Verified {severity} detection {inspection_id}.",
    )
    return incident


def transition_incident(
    store: EntityStore, incident_id: str, *, state: str, actor: str, reason: str = ""
) -> dict:
    """Move an incident between states, validating the transition."""
    incident = _require(store, "incidents", incident_id)
    current = incident.get("state", "New")
    if current in INCIDENT_TERMINAL:
        raise ValueError(f"Incident {incident_id} is {current} and cannot be changed.")
    if state not in INCIDENT_STATES:
        raise ValueError(f"{state!r} is not a valid incident state.")

    updated = {**incident, "state": state}
    if state == "Closed":
        updated["closed_at"] = now_iso()
        updated["closure_reason"] = reason
    store.put("incidents", updated)
    record_event(
        store,
        actor=actor,
        kind="Incident",
        record_id=incident_id,
        action=f"State changed to {state}",
        previous=current,
        next_state=state,
        reason=reason,
    )
    return updated


def close_incident(store: EntityStore, incident_id: str, *, actor: str, reason: str) -> dict:
    """Close an incident. Separate from completing its work order, by design."""
    return transition_incident(
        store, incident_id, state="Closed", actor=actor, reason=reason
    )


# --- work orders ------------------------------------------------------------------


def create_work_order(
    store: EntityStore,
    incident_id: str,
    *,
    crew_id: str,
    actor: str,
    instructions: str = "",
    destination: str | None = None,
) -> dict:
    """Assign a crew. Moves the incident to Dispatched as a side effect.

    The two records move together but stay separate: the work order owns crew and
    execution, the incident owns the operational obligation.
    """
    incident = _require(store, "incidents", incident_id)
    if incident.get("state") in ("New", "Acknowledged"):
        raise ValueError(
            f"Incident {incident_id} is {incident['state']}. Verify it before dispatch."
        )
    if incident.get("work_order_id"):
        return _require(store, "work_orders", incident["work_order_id"])

    crew = _require(store, "crews", crew_id)
    work_order_id = store.next_id("work_orders", "WO-", width=5, start=24_880)
    order = store.put(
        "work_orders",
        {
            "id": work_order_id,
            "incident_id": incident_id,
            "crew_id": crew_id,
            "crew_name": crew.get("name"),
            "destination": destination or incident.get("asset_id"),
            "state": "Dispatched",
            "instructions": instructions,
            "dispatched_at": now_iso(),
            "acknowledged_at": None,
            "arrived_at": None,
            "completed_at": None,
            "completion_note": None,
            "final_blockage": None,
            "evidence": [],
        },
    )
    store.put("incidents", {**incident, "work_order_id": work_order_id, "state": "Dispatched"})
    store.put("crews", {**crew, "state": "Assigned", "work_order_id": work_order_id})
    record_event(
        store,
        actor=actor,
        kind="Work order",
        record_id=work_order_id,
        action="Unit assigned",
        previous="Unassigned",
        next_state="Dispatched",
        reason=f"{crew.get('name')} assigned to {incident_id}.",
    )
    return order


def transition_work_order(
    store: EntityStore, work_order_id: str, *, state: str, actor: str, reason: str = ""
) -> dict:
    order = _require(store, "work_orders", work_order_id)
    current = order.get("state", "Dispatched")
    if current in WORK_ORDER_TERMINAL:
        raise ValueError(f"Work order {work_order_id} is {current} and cannot be changed.")
    if state not in WORK_ORDER_STATES:
        raise ValueError(f"{state!r} is not a valid work-order state.")

    updated = {**order, "state": state}
    stamp_field = {
        "Acknowledged": "acknowledged_at",
        "On site": "arrived_at",
        "Completed": "completed_at",
    }.get(state)
    if stamp_field:
        updated[stamp_field] = now_iso()
    store.put("work_orders", updated)

    # Mirror the field state onto the incident so the two screens agree, without
    # implying closure.
    if state in ("On site", "Acknowledged", "En route"):
        incident = store.get("incidents", order["incident_id"])
        if incident and incident.get("state") not in INCIDENT_TERMINAL:
            mirrored = "On site" if state == "On site" else "Dispatched"
            store.put("incidents", {**incident, "state": mirrored})

    record_event(
        store,
        actor=actor,
        kind="Work order",
        record_id=work_order_id,
        action=f"State changed to {state}",
        previous=current,
        next_state=state,
        reason=reason,
    )
    return updated


def complete_work_order(
    store: EntityStore,
    work_order_id: str,
    *,
    actor: str,
    completion_note: str,
    final_blockage: float | None = None,
    evidence: list[str] | None = None,
) -> dict:
    """Field work finished.

    Note what this does **not** do: it never closes the incident. Completion is a claim
    by the crew; closure is a separate verification of the mitigation, and the V1
    dispatch screen says so on screen. Merging them would let a crew close its own
    incident with no second pair of eyes.
    """
    order = _require(store, "work_orders", work_order_id)
    updated = store.put(
        "work_orders",
        {
            **order,
            "state": "Completed",
            "completed_at": now_iso(),
            "completion_note": completion_note,
            "final_blockage": final_blockage,
            "evidence": evidence or order.get("evidence", []),
        },
    )
    store.put("crews", {**(_require(store, "crews", order["crew_id"])), "state": "Available",
                        "work_order_id": None})
    record_event(
        store,
        actor=actor,
        kind="Work order",
        record_id=work_order_id,
        action="Work completed",
        previous=order.get("state", "On site"),
        next_state="Completed",
        reason=completion_note,
    )
    incident = store.get("incidents", order["incident_id"])
    if incident and incident.get("state") not in INCIDENT_TERMINAL:
        record_event(
            store,
            actor="System",
            kind="Incident",
            record_id=incident["id"],
            action="Awaiting mitigation verification",
            previous=incident.get("state", ""),
            next_state="Awaiting mitigation verification",
            reason="Field work completed. Incident closure requires separate verification.",
        )
    return updated


# --- shared helpers ---------------------------------------------------------------


def _require(store: EntityStore, collection: str, record_id: str) -> dict:
    record = store.get(collection, record_id)
    if not record:
        raise LookupError(f"No {collection} record {record_id!r}.")
    return record


def purge(store: EntityStore) -> None:
    """Wipe every operations collection. Seeding only."""
    for collection in (
        "users", "sessions", "sources", "assets", "inspections",
        "incidents", "work_orders", "crews", "audit_events", "access_requests", "sequences",
    ):
        path = getattr(store, "root", None)
        if path is not None:
            target = path / f"{collection}.jsonl"
            if target.exists():
                target.unlink()
