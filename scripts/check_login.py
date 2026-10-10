#!/usr/bin/env python3
"""Directly verify a seeded account's password against its stored hash.

No server, no network, no shell quoting of the password. Answers one question: does the
operator credential the seeder printed actually verify against what is on disk? If this
says True, any login 401 is a transport problem (wrong port, stale browser bundle, a
command typed into the server's own tab), not the backend.

    PYTHONPATH=src python3 scripts/check_login.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drainwatch.ops.security import verify_password  # noqa: E402
from drainwatch.ops.store import get_ops_store  # noqa: E402

CREDENTIALS = [
    ("operator@drainwatch.in", "Operator!2026"),
    ("admin@drainwatch.in", "Admin!2026"),
]


def main() -> int:
    store = get_ops_store()
    users = store.all("users")
    print(f"users in store: {len(users)}")
    ok = True
    for email, password in CREDENTIALS:
        user = next((u for u in users if u.get("email") == email), None)
        if not user:
            print(f"  [FAIL] {email}: not found (did you run scripts/seed_ops.py?)")
            ok = False
            continue
        verifies = verify_password(password, user.get("salt", ""), user.get("password_hash", ""))
        print(f"  [{'ok  ' if verifies else 'FAIL'}] {email}: id={user['id']} role={user.get('role')} password_verifies={verifies}")
        ok = ok and verifies
    print("\nbackend credentials are correct" if ok else "\nsomething is off — paste this output")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
