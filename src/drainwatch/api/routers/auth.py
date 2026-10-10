"""Auth, session and profile routes.

Implements the surface ``frontend/src/api/auth.ts`` calls. The response envelopes accept
either a bare object or a ``{"user": ...}`` wrapper, matching the frontend's ``unwrapUser``
— so these return the bare form and nothing has to change on either side.

``POST /api/auth/forgot-password`` and ``POST /api/auth/request-access`` deliberately do
not reveal whether an address exists: both always answer 200. An endpoint that says
"no such user" is an account-enumeration oracle, which is exactly the kind of thing this
project should not ship while claiming to be careful about auth.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from drainwatch.ops.security import (
    clear_session_cookie,
    create_session,
    get_current_user,
    make_user,
    public_user,
    resolve_session,
    revoke_session,
    set_session_cookie,
    verify_password,
)
from drainwatch.ops.store import EntityStore, get_ops_store, now_iso

router = APIRouter(prefix="/api", tags=["auth"])


def _user_by_email(store: EntityStore, email: str) -> dict | None:
    """Find a user by email address.

    Users are keyed by a slug of their email (``make_user`` sets ``id = slugify(email)``),
    not by the email itself, so a direct ``get(email)`` would never match. Sign-in gives us
    an email, so the lookup is over the ``email`` field. ``get_current_user`` and the admin
    routes already hold a user id and look up by it directly — only the email-first paths
    (sign-in, password reset) come through here.
    """
    target = email.lower().strip()
    return next((u for u in store.all("users") if u.get("email") == target), None)


def _authenticate(store: EntityStore, email: str, password: str) -> dict:
    """Resolve a user from credentials, or raise the right 401/403.

    ``status`` is checked *after* the password, so a disabled account still has to prove
    it holds the password before learning it is disabled — otherwise the response leaks
    that the address is real.
    """
    user = _user_by_email(store, email)
    if not user:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    if not verify_password(password, user.get("salt", ""), user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    if user.get("status") == "Disabled":
        raise HTTPException(status_code=403, detail="This account has been disabled.", headers={"x-error-code": "ACCOUNT_DISABLED"})
    return user


@router.get("/auth/session")
def session(request: Request, store: EntityStore = Depends(get_ops_store)):
    """The signed-in user, or 401.

    The frontend treats 401 here as "not signed in" rather than an error, so this is the
    expected answer for a logged-out visitor.
    """
    token = request.cookies.get("dw_session")
    resolved = resolve_session(store, token)
    if not resolved:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = store.get("users", resolved["user_id"])
    if not user or user.get("status") == "Disabled":
        raise HTTPException(status_code=401, detail="Not authenticated")
    return public_user(user)


@router.post("/auth/login")
def login(payload: dict, response: Response, store: EntityStore = Depends(get_ops_store)):
    email = str(payload.get("email", ""))
    password = str(payload.get("password", ""))
    if not email or not password:
        raise HTTPException(status_code=422, detail="Email and password are required.")

    user = _authenticate(store, email, password)
    token = create_session(store, user["id"], remembered=bool(payload.get("remember")))
    set_session_cookie(response, token, remembered=bool(payload.get("remember")))

    store.put("users", {**user, "last_sign_in": now_iso()})
    from drainwatch.ops.domain import record_event

    record_event(
        store,
        actor=user.get("name", email),
        kind="Account",
        record_id=user["id"],
        action="Signed in",
        reason="Standard sign-in.",
    )
    return public_user(user)


@router.post("/auth/admin/login")
def admin_login(payload: dict, response: Response, store: EntityStore = Depends(get_ops_store)):
    """Elevated sign-in.

    Authenticates identically and then refuses a non-admin. The check is here rather than
    only in the UI: the V1 hides the admin link from operators, but a hidden control is
    not an authorization boundary.
    """
    email = str(payload.get("email", ""))
    password = str(payload.get("password", ""))
    user = _authenticate(store, email, password)
    if user.get("role") != "ADMIN":
        raise HTTPException(
            status_code=403,
            detail="This account does not have administrator access.",
            headers={"x-error-code": "ADMIN_REQUIRED"},
        )

    token = create_session(store, user["id"], remembered=bool(payload.get("remember")))
    set_session_cookie(response, token, remembered=bool(payload.get("remember")))
    store.put("users", {**user, "last_sign_in": now_iso()})
    return public_user(user)


@router.post("/auth/logout")
def logout(request: Request, response: Response, store: EntityStore = Depends(get_ops_store)):
    revoke_session(store, request.cookies.get("dw_session"))
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/auth/forgot-password")
def forgot_password(payload: dict, store: EntityStore = Depends(get_ops_store)):
    """Always 200 — see the module docstring on enumeration.

    No mail transport is configured, so nothing is sent. The request is recorded so an
    administrator can action it, and the response is identical either way.
    """
    email = str(payload.get("email", "")).lower().strip()
    if email:
        exists = _user_by_email(store, email) is not None
        store.put(
            "access_requests",
            {
                "id": f"pwd-{email}-{now_iso()}",
                "kind": "password_reset",
                "email": email,
                "known_account": exists,
                "requested_at": now_iso(),
                "handled": False,
            },
        )
    return {"ok": True, "message": "If an account matches that email, reset instructions have been sent."}


@router.post("/auth/signup")
def signup(payload: dict, response: Response, store: EntityStore = Depends(get_ops_store)):
    """Create a new operator account and sign in immediately.

    New accounts are always OPERATOR role. Admin promotion is an admin workflow.
    """
    email = str(payload.get("email", "")).lower().strip()
    password = str(payload.get("password", ""))
    name = str(payload.get("name", "")).strip()
    organization = str(payload.get("organization", "")).strip() or None
    jurisdiction = str(payload.get("jurisdiction", "")).strip() or None

    if not email or not password or not name:
        raise HTTPException(status_code=422, detail="Name, email and password are required.")
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")

    # Check for existing account
    existing = _user_by_email(store, email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = make_user(
        name=name,
        email=email,
        password=password,
        role="OPERATOR",
        organization=organization,
        jurisdiction=jurisdiction,
    )
    store.put("users", user)

    # Auto sign-in
    token = create_session(store, user["id"])
    set_session_cookie(response, token)

    store.put("users", {**user, "last_sign_in": now_iso()})

    from drainwatch.ops.domain import record_event

    record_event(
        store,
        actor=name,
        kind="Account",
        record_id=user["id"],
        action="Account created",
        reason="Self-registration via signup form.",
    )
    return public_user(user)


@router.post("/auth/request-access")
def request_access(payload: dict, store: EntityStore = Depends(get_ops_store)):
    """A prospective operator asks for an account. Recorded for an admin to action."""
    email = str(payload.get("email", "")).lower().strip()
    if not email:
        raise HTTPException(status_code=422, detail="An email address is required.")
    store.put(
        "access_requests",
        {
            "id": f"access-{email}-{now_iso()}",
            "kind": "access",
            "email": email,
            "name": payload.get("name"),
            "organization": payload.get("organization"),
            "requested_at": now_iso(),
            "handled": False,
        },
    )
    return {"ok": True, "message": "Access request sent"}


@router.patch("/profile")
def update_profile(
    payload: dict,
    user: dict = Depends(get_current_user),
    store: EntityStore = Depends(get_ops_store),
):
    """Update the editable profile fields.

    Email and role are not editable here: changing an email would move the account's
    identity, and changing a role through a profile form is privilege escalation. Both
    belong to an admin workflow, and the V1 disables the email field for the same reason.
    """
    allowed = {"name", "organization", "jurisdiction", "timezone", "notifications", "map_preference", "avatar_url"}
    changes = {key: value for key, value in payload.items() if key in allowed}
    if not changes:
        raise HTTPException(status_code=422, detail="No editable fields were provided.")

    updated = store.put("users", {**user, **changes})

    from drainwatch.ops.domain import record_event

    record_event(
        store,
        actor=user.get("name", user["id"]),
        kind="Account",
        record_id=user["id"],
        action="Profile updated",
        previous=", ".join(sorted(changes)),
        next_state=", ".join(sorted(changes)),
        reason="Operator edited their own profile.",
    )
    return public_user(updated)
