#!/usr/bin/env python3
"""End-to-end smoke test of the V1 operations API.

Seeds a demo dataset, then drives the real FastAPI app through a TestClient exactly as the
V1 frontend would: sign in, read every operator surface, confirm the admin boundary holds,
and confirm an operator is refused the admin routes. No model and no network are needed —
model loading is skipped, and ``/api/analyze`` (which needs a checkpoint) is not called.

    PYTHONPATH=src python3 scripts/verify_v1.py

Exit code 0 means every checked endpoint answered as the frontend expects.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import seed_ops  # noqa: E402  (scripts/seed_ops.py)
from drainwatch.api import main  # noqa: E402

OPERATOR = ("operator@drainwatch.in", "Operator!2026")
ADMIN = ("admin@drainwatch.in", "Admin!2026")

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    mark = "ok  " if condition else "FAIL"
    if condition:
        _passed += 1
    else:
        _failed += 1
    print(f"  [{mark}] {label}{(' — ' + detail) if detail and not condition else ''}")


def main_() -> int:
    # Skip the real model load — this verifies the operations plumbing, not inference.
    main._load_model_quietly = lambda: None  # type: ignore[assignment]

    print("seeding demo dataset (local store)...")
    seed_ops.seed(clear=True)

    from fastapi.testclient import TestClient

    print("\nrouting:")
    routes = {r.path for r in main.app.routes if hasattr(r, "path")}
    for path in ("/api/auth/login", "/api/overview", "/api/inspections", "/api/incidents",
                 "/api/work-orders", "/api/crews", "/api/sources", "/api/assets",
                 "/api/audit", "/api/map/records", "/api/system/health", "/api/analyze",
                 "/api/admin/users"):
        check(f"route {path} registered", path in routes, "missing")

    with TestClient(main.app) as client:
        print("\nauth:")
        bad = client.post("/api/auth/login", json={"email": OPERATOR[0], "password": "wrong"})
        check("wrong password is 401", bad.status_code == 401, f"got {bad.status_code}")

        login = client.post("/api/auth/login", json={"email": OPERATOR[0], "password": OPERATOR[1]})
        check("operator login is 200", login.status_code == 200, f"got {login.status_code}: {login.text[:120]}")
        if login.status_code == 200:
            body = login.json()
            check("login returns operator role", body.get("role") == "OPERATOR", str(body.get("role")))

        session = client.get("/api/auth/session")
        check("session resolves after login", session.status_code == 200, f"got {session.status_code}")

        print("\noperator surfaces:")
        for path in ("/api/overview", "/api/inspections", "/api/incidents", "/api/work-orders",
                     "/api/crews", "/api/sources", "/api/assets", "/api/audit",
                     "/api/map/records", "/api/system/health"):
            res = client.get(path)
            check(f"GET {path} is 200", res.status_code == 200, f"got {res.status_code}")

        overview = client.get("/api/overview").json()
        counts = overview.get("counts", {})
        check("overview has real counts", "open_incidents" in counts and counts.get("sources", 0) >= 5,
              str(counts))
        check("priority queue populated", len(overview.get("priority_queue", [])) >= 1,
              str(len(overview.get("priority_queue", []))))

        insp = client.get("/api/inspections").json()
        check("inspections seeded", len(insp) >= 5, f"{len(insp)} returned")

        maprecs = client.get("/api/map/records").json()
        check("map records have coordinates", all(len(r.get("position", [])) == 2 for r in maprecs),
              "a record is missing a position")

        print("\nadmin boundary:")
        denied = client.get("/api/admin/users")
        check("operator refused /api/admin/users (403)", denied.status_code == 403, f"got {denied.status_code}")

        client.post("/api/auth/logout")
        admin_login = client.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        check("admin login is 200", admin_login.status_code == 200, f"got {admin_login.status_code}")
        allowed = client.get("/api/admin/users")
        check("admin allowed /api/admin/users (200)", allowed.status_code == 200, f"got {allowed.status_code}")
        if allowed.status_code == 200:
            check("admin sees both seeded users", len(allowed.json()) >= 2, str(len(allowed.json())))

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main_())
