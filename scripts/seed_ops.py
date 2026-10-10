#!/usr/bin/env python3
"""Seed a coherent operations dataset for development and demos.

Everything here is linked, not scattered: a source watches an asset, an inspection comes
from that source, a verified inspection opens an incident, an incident is dispatched to a
crew as a work order, and every step leaves an audit event. The records are realistic but
plainly fictional Delhi municipal data — the kind of dataset the V1 interface was designed
to render, built through the real domain functions so the state machines and the audit
trail are exercised exactly as a live operator would exercise them.

Credentials for the two seeded accounts are printed at the end.

    python3 scripts/seed_ops.py                 # local store
    python3 scripts/seed_ops.py --clear         # wipe ops collections first
    DRAINWATCH_OPS_TABLE=... python3 scripts/seed_ops.py   # a configured AWS table
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drainwatch.ops import domain  # noqa: E402
from drainwatch.ops.security import make_user  # noqa: E402
from drainwatch.ops.store import get_ops_store, now_iso  # noqa: E402

OPERATOR = {"email": "operator@drainwatch.in", "password": "Operator!2026", "name": "Rishabh Singh"}
ADMIN = {"email": "admin@drainwatch.in", "password": "Admin!2026", "name": "A. Mehta"}


def _hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")


def seed(clear: bool) -> None:
    store = get_ops_store()
    if clear and hasattr(store, "root"):
        domain.purge(store)
        print("cleared ops collections")

    # --- users ---
    store.put("users", make_user(
        name=OPERATOR["name"], email=OPERATOR["email"], password=OPERATOR["password"],
        role="OPERATOR", title="Operations Operator", organization="Delhi NCR Drainage",
        jurisdiction="Delhi NCR",
    ))
    store.put("users", make_user(
        name=ADMIN["name"], email=ADMIN["email"], password=ADMIN["password"],
        role="ADMIN", title="System Administrator", organization="Delhi NCR Drainage",
        jurisdiction="Delhi NCR",
    ))

    # --- crews (positioned for the map) ---
    crews = [
        {"id": "UNIT-07", "name": "Drainage Unit 07", "supervisor": "A. Mehta", "state": "Available", "sector": "Sector 4", "eta_minutes": 12, "latitude": 28.6128, "longitude": 77.2041, "members": 2},
        {"id": "UNIT-03", "name": "Drainage Unit 03", "supervisor": "R. Kumar", "state": "Available", "sector": "Sector 2", "eta_minutes": 18, "latitude": 28.6255, "longitude": 77.1835, "members": 2},
        {"id": "UNIT-11", "name": "Drainage Unit 11", "supervisor": "P. Singh", "state": "Busy", "sector": "Sector 6", "eta_minutes": 34, "latitude": 28.6402, "longitude": 77.2231, "members": 3},
    ]
    for crew in crews:
        store.put("crews", crew)

    # --- sources (cameras / drone) with coordinates ---
    sources = [
        {"id": "CCTV-17", "type": "CCTV", "status": "Active", "coverage": "Drain corridor camera", "sector": "Sector 4", "latitude": 28.6148, "longitude": 77.2078, "last_frame": _hours_ago(0.1), "last_analysis": _hours_ago(0.1), "health": "Current"},
        {"id": "CCTV-24", "type": "CCTV", "status": "Active", "coverage": "Municipal road camera", "sector": "Sector 6", "latitude": 28.6350, "longitude": 77.2205, "last_frame": _hours_ago(0.05), "last_analysis": _hours_ago(0.05), "health": "Current"},
        {"id": "CCTV-31", "type": "CCTV", "status": "Active", "coverage": "Drain corridor camera", "sector": "Sector 7", "latitude": 28.6291, "longitude": 77.1781, "last_frame": _hours_ago(0.2), "last_analysis": _hours_ago(0.4), "health": "Current"},
        {"id": "CCTV-42", "type": "CCTV", "status": "Delayed", "coverage": "Municipal road camera", "sector": "Sector 8", "latitude": 28.6460, "longitude": 77.2360, "last_frame": _hours_ago(0.5), "last_analysis": _hours_ago(0.5), "health": "Frame delivery delayed"},
        {"id": "DRONE-03", "type": "Drone", "status": "Active", "coverage": "Periodic inspection source", "sector": "Sector 2", "latitude": 28.5970, "longitude": 77.1890, "last_frame": _hours_ago(0.3), "last_analysis": _hours_ago(0.3), "health": "Current"},
    ]
    for source in sources:
        store.put("sources", source)

    # --- assets (drains) with coordinates ---
    assets = [
        {"id": "D-1042", "name": "Drain D-1042", "type": "Open drainage channel", "sector": "Sector 4", "latitude": 28.6139, "longitude": 77.2090, "source_id": "CCTV-17"},
        {"id": "D-1098", "name": "Drain D-1098", "type": "Open drainage channel", "sector": "Sector 6", "latitude": 28.6341, "longitude": 77.2218, "source_id": "CCTV-24"},
        {"id": "C-0208", "name": "Culvert C-0208", "type": "Culvert", "sector": "Sector 2", "latitude": 28.5970, "longitude": 77.1890, "source_id": "DRONE-03"},
        {"id": "I-0031", "name": "Inlet I-0031", "type": "Inlet", "sector": "Sector 7", "latitude": 28.6290, "longitude": 77.1780, "source_id": "CCTV-31"},
        {"id": "CH-0441", "name": "Channel CH-0441", "type": "Open drainage channel", "sector": "Sector 5", "latitude": 28.6060, "longitude": 77.2380, "source_id": "DRONE-03"},
        {"id": "O-0118", "name": "Outfall O-0118", "type": "Outfall", "sector": "Sector 3", "latitude": 28.6420, "longitude": 77.2250, "source_id": "CCTV-42"},
    ]
    for asset in assets:
        store.put("assets", asset)

    # --- the scenario: inspections → verification → incidents → dispatch ---
    # (source, asset, blockage, previous, verify?, incident?, dispatch_crew, complete?)
    scenario = [
        ("CCTV-17", "D-1042", 0.784, 0.612, True, True, "UNIT-07", False),
        ("CCTV-24", "D-1098", 0.746, 0.528, False, False, None, False),
        ("DRONE-03", "C-0208", 0.568, 0.417, True, True, None, False),
        ("CCTV-31", "I-0031", 0.721, 0.742, True, True, "UNIT-03", True),
        ("DRONE-03", "CH-0441", 0.486, 0.441, False, False, None, False),
        ("CCTV-42", "O-0118", 0.439, 0.396, False, False, None, False),
        ("CCTV-17", "D-1042", 0.182, None, False, False, None, False),  # a clear reading too
    ]

    actor = OPERATOR["name"]
    for i, (source_id, asset_id, blockage, previous, verify, incident, crew_id, complete) in enumerate(scenario):
        inspection = domain.create_inspection(
            store,
            source_id=source_id,
            asset_id=asset_id,
            blockage=blockage,
            confidence=0.90 - i * 0.01,
            classes=[],
            model_version="DrainSeg v1",
            captured_at=_hours_ago(1.0 - i * 0.1),
            previous_blockage=previous,
        )
        asset = store.get("assets", asset_id)
        store.put("assets", {
            **asset,
            "latest_inspection_id": inspection["id"],
            "condition": inspection["severity"].capitalize(),
        })

        if verify:
            domain.verify_inspection(store, inspection["id"], actor=actor)
        if incident:
            created = domain.create_incident(store, inspection["id"], actor=actor)
            store.put("assets", {**store.get("assets", asset_id), "incident_id": created["id"]})
            if crew_id:
                order = domain.create_work_order(
                    store, created["id"], crew_id=crew_id, actor=actor,
                    instructions="Clear accumulated solid waste from the functional channel; "
                                 "verify unobstructed flow and submit completion evidence.",
                )
                domain.transition_work_order(store, order["id"], state="Acknowledged", actor=crews_name(crews, crew_id))
                domain.transition_work_order(store, order["id"], state="On site", actor=crews_name(crews, crew_id))
                if complete:
                    # Work completed — but the incident stays open, as the model demands.
                    domain.complete_work_order(
                        store, order["id"], actor=crews_name(crews, crew_id),
                        completion_note="Solid waste removed from channel entrance.",
                        final_blockage=0.184, evidence=["completion-1.jpg", "completion-2.jpg"],
                    )

    counts = {c: len(store.all(c)) for c in ("users", "sources", "assets", "inspections", "incidents", "work_orders", "crews", "audit_events")}
    print("seeded:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    print()
    print("Sign in with:")
    print(f"  operator  {OPERATOR['email']}  /  {OPERATOR['password']}")
    print(f"  admin     {ADMIN['email']}  /  {ADMIN['password']}")


def crews_name(crews: list[dict], crew_id: str) -> str:
    for crew in crews:
        if crew["id"] == crew_id:
            return crew["name"]
    return crew_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="wipe ops collections first")
    args = parser.parse_args()
    seed(clear=args.clear)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
