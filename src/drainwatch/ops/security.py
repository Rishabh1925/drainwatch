"""Authentication primitives: password hashing, session tokens, FastAPI guards.

Deliberately stdlib-only. The API container already carries torch and transformers; a
passlib/bcrypt/itsdangerous stack would be three more wheels to pin, and none of them
earns its place here. ``hashlib.pbkdf2_hmac`` is in the standard library, is FIPS-grade
for password storage, and needs no build step.

Three decisions worth stating, because each is a place this could have been done weakly:

* **Passwords use PBKDF2-HMAC-SHA256 with a per-user random salt and a high iteration
  count.** The salt defeats rainbow tables; the iteration count makes each guess cost
  real time. Verification is constant-time (``hmac.compare_digest``) so a timing side
  channel cannot recover a password byte by byte.

* **Session tokens are stored hashed.** The cookie carries a random token; the store
  keeps only its SHA-256. A leak of the session collection therefore yields no usable
  cookie — the same reasoning as password hashing, applied to the session itself.

* **Admin is enforced server-side, never by the client.** The V1 hides admin nav behind
  ``user.role === "ADMIN"``, which is a UI affordance and nothing more; the guard below
  is what actually refuses an operator. Anything else would be security theatre.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response

from drainwatch.ops.store import EntityStore, get_ops_store, now_iso, slugify

PBKDF2_ITERATIONS = 240_000
SESSION_COOKIE = "dw_session"
SESSION_TTL_HOURS = 12
SESSION_TTL_DAYS_REMEMBERED = 30

# SameSite=Lax so the cookie rides a top-level navigation back into the app but not a
# cross-site POST. Secure is off when the app is served over plain HTTP in local dev —
# set DRAINWATCH_COOKIE_SECURE=1 in production, where App Runner/Fargate terminates TLS.
COOKIE_SECURE = os.environ.get("DRAINWATCH_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}


def hash_password(password: str, *, salt: bytes | None = None) -> tuple[str, str]:
    """Return ``(salt_hex, hash_hex)`` for a password."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Constant-time password check. Never short-circuits on a partial match."""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(expected.hex(), hash_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    store: EntityStore, user_id: str, *, remembered: bool = False
) -> str:
    """Mint a session and return the raw token for the cookie.

    Only the token's hash is persisted — see the module docstring.
    """
    token = secrets.token_urlsafe(32)
    ttl = timedelta(days=SESSION_TTL_DAYS_REMEMBERED if remembered else 0)
    expires = datetime.now(timezone.utc) + (
        ttl if remembered else timedelta(hours=SESSION_TTL_HOURS)
    )
    store.put(
        "sessions",
        {
            "id": _token_hash(token),
            "user_id": user_id,
            "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "remembered": remembered,
        },
    )
    return token


def resolve_session(store: EntityStore, token: str | None) -> dict | None:
    """The session record for a raw token, or None when absent or expired."""
    if not token:
        return None
    session = store.get("sessions", _token_hash(token))
    if not session:
        return None
    expires_at = session.get("expires_at")
    if expires_at and expires_at < now_iso():
        store.put("sessions", {**session, "revoked": True})
        return None
    if session.get("revoked"):
        return None
    return session


def revoke_session(store: EntityStore, token: str | None) -> None:
    if not token:
        return
    session = store.get("sessions", _token_hash(token))
    if session:
        store.put("sessions", {**session, "revoked": True})


def set_session_cookie(response: Response, token: str, *, remembered: bool = False) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,  # not readable by JavaScript — an XSS cannot exfiltrate it
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=int(
            timedelta(days=SESSION_TTL_DAYS_REMEMBERED if remembered else 0).total_seconds()
            or timedelta(hours=SESSION_TTL_HOURS).total_seconds()
        ),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# --- public shape -----------------------------------------------------------------


def public_user(user: dict) -> dict:
    """The user record as the frontend expects it — never the password material."""
    return {
        "id": user["id"],
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", "OPERATOR"),
        "title": user.get("title"),
        "organization": user.get("organization"),
        "jurisdiction": user.get("jurisdiction"),
        "status": user.get("status", "Active"),
        "last_sign_in": user.get("last_sign_in"),
        "timezone": user.get("timezone", "Asia/Kolkata"),
        "notifications": user.get("notifications", True),
        "map_preference": user.get("map_preference"),
        "avatar_url": user.get("avatar_url"),
    }


# --- FastAPI guards ---------------------------------------------------------------


def get_current_user(
    dw_session: str | None = Cookie(default=None),
    store: EntityStore = Depends(get_ops_store),
) -> dict:
    """Resolve the signed-in user, or 401.

    401 rather than 403 when there is no session: the frontend distinguishes them
    (it prompts a re-login on 401 and a permission message on 403), so conflating the
    two would send an expired session down the wrong UI path.
    """
    session = resolve_session(store, dw_session)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = store.get("users", session["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("status") == "Disabled":
        raise HTTPException(status_code=403, detail="This account has been disabled.")
    return user


def get_current_user_optional(
    dw_session: str | None = Cookie(default=None),
    store: EntityStore = Depends(get_ops_store),
) -> dict | None:
    """Same as :func:`get_current_user` but returns None instead of raising.

    Used by ``GET /api/auth/session``, where "not signed in" is a normal answer rather
    than an error.
    """
    try:
        return get_current_user(dw_session=dw_session, store=store)
    except HTTPException:
        return None


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Admin-only guard. The single place admin authorization is decided."""
    if user.get("role") != "ADMIN":
        raise HTTPException(
            status_code=403, detail="You do not have permission to perform this action."
        )
    return user


def make_user(
    *,
    name: str,
    email: str,
    password: str,
    role: str = "OPERATOR",
    title: str | None = None,
    organization: str | None = None,
    jurisdiction: str | None = None,
    status: str = "Active",
) -> dict:
    """Build a user record with hashed credentials. The only way a user is constructed."""
    salt_hex, hash_hex = hash_password(password)
    return {
        "id": slugify(email),
        "name": name,
        "email": email.lower().strip(),
        "role": role.upper(),
        "title": title,
        "organization": organization,
        "jurisdiction": jurisdiction,
        "status": status,
        "salt": salt_hex,
        "password_hash": hash_hex,
        "timezone": "Asia/Kolkata",
        "notifications": True,
        "map_preference": "Operational records",
    }
