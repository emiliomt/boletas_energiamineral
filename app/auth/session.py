"""Authentication gate for web and API routes.

Reworked to rely on Clerk sessions:
- The frontend renders Clerk's SignIn/UserButton UI.
- The backend verifies the Clerk session token on each request via
  ``authenticate_request``, accepting a token from the
  ``Authorization: Bearer`` header or the ``__session`` cookie.
- Browser navigations that are signed in on the Clerk client
  (``__client_uat``) but have no usable app ``__session`` cookie are sent
  through the Clerk handshake so the session is persisted on this host.
  Skipping that step bounces a successful sign-in back to ``/login``.

No server-managed username/password login remains; the previous Supabase
form is removed in favor of Clerk. When Clerk is not configured, protected
routes fail closed (401 for API, redirect to /login for web)."""
from __future__ import annotations

from fastapi import HTTPException, Request
from app.auth.clerk_handshake import (
    AuthHandshake,
    HandshakeAction,
    maybe_handshake,
    resolve_handshake_return,
    safe_next_path,
)
from app.config import settings

authenticate_request = None  # type: ignore[assignment]
AuthenticateRequestOptions = None  # type: ignore[assignment]


def _load_clerk_authenticate():
    """Import whichever layout this clerk-backend-api release ships."""
    try:
        from clerk_backend_api.security import authenticate_request as auth_req
        from clerk_backend_api.security.types import AuthenticateRequestOptions as options_cls

        return auth_req, options_cls
    except Exception:
        pass
    try:
        from clerk_backend_api.jwks_helpers import (
            AuthenticateRequestOptions as options_cls,
            authenticate_request as auth_req,
        )

        return auth_req, options_cls
    except Exception:
        return None, None


authenticate_request, AuthenticateRequestOptions = _load_clerk_authenticate()


SESSION_KEY = "admin_email"  # kept for backwards-compatibility; no longer used

class AuthRedirect(Exception):
    """Raised by require_admin_web when unauthenticated; app/main.py
    registers an exception handler that turns this into a redirect to
    /login?next=<path>."""

    def __init__(self, next_path: str):
        self.next_path = next_path


def _is_clerk_configured() -> bool:
    # Either a secret key or a JWT public key must be present to verify tokens.
    return bool(settings.clerk_secret_key or settings.clerk_jwt_key)


def _reason_code(reason: object) -> str:
    value = getattr(reason, "value", None)
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    name = getattr(reason, "name", None)
    if isinstance(name, str) and name:
        return name
    return "unauthorized"


def _auth_options():
    kwargs = {
        "secret_key": settings.clerk_secret_key,
        "jwt_key": settings.clerk_jwt_key,
        "authorized_parties": settings.clerk_authorized_parties_list or None,
    }
    fields = getattr(AuthenticateRequestOptions, "__dataclass_fields__", {})
    if "accepts_token" in fields:
        kwargs["accepts_token"] = ["session_token"]
    return AuthenticateRequestOptions(**kwargs)


def _verify_clerk_request(request: Request) -> tuple[bool, str | None, str | None, str | None]:
    """Returns (is_authenticated, user_id, email, reason)."""
    if authenticate_request is None or AuthenticateRequestOptions is None:
        return (False, None, None, "clerk_sdk_missing")
    if not _is_clerk_configured():
        return (False, None, None, "clerk_not_configured")
    opts = _auth_options()
    state = authenticate_request(request, opts)
    if not getattr(state, "is_signed_in", False) and not getattr(state, "is_authenticated", False):
        reason = getattr(state, "reason", None)
        return (False, None, None, _reason_code(reason) if reason is not None else "unauthorized")
    payload = getattr(state, "payload", {}) or {}
    user_id = str(payload.get("sub")) if payload.get("sub") is not None else None
    # Best-effort email extraction — Clerk JWT v2 commonly includes "email".
    email = None
    for key in ("email", "email_address", "primary_email_address"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            email = v
            break
    return (True, user_id, email, None)

def current_admin(request: Request) -> str | None:
    """Best-effort current admin identity for stamping actions.
    Returns email if available, else user id, else None.
    """
    ok, user_id, email, _ = _verify_clerk_request(request)
    if ok:
        return email or user_id
    # Back-compat: return any legacy session value if present
    try:
        return request.session.get(SESSION_KEY)
    except Exception:
        return None

def require_admin_api(request: Request) -> str:
    ok, user_id, email, reason = _verify_clerk_request(request)
    if not ok:
        # Fail closed; signal to clients that auth is required.
        raise HTTPException(status_code=401, detail=reason or "Not authenticated")
    # Optional admin allowlist by email; deny if configured but we couldn't determine an email.
    allow = settings.admin_allowlist
    if allow:
        normalized = (email or "").strip().lower()
        if not normalized or normalized not in allow:
            raise HTTPException(status_code=403, detail="Not authorized")
    # Return a stable identifier (prefer email, else user id)
    return email or (user_id or "")


def _raise_handshake(action: HandshakeAction) -> None:
    raise AuthHandshake(location=action.location, set_cookies=action.set_cookies)


def require_admin_web(request: Request) -> str:
    returned = resolve_handshake_return(request)
    if returned is not None:
        _raise_handshake(returned)
    ok, user_id, email, reason = _verify_clerk_request(request)
    if not ok:
        action = maybe_handshake(request, reason)
        if action is not None:
            _raise_handshake(action)
        # Redirect to login preserving the in-site path only (open-redirect safe).
        # Never send the user back to /login itself; that recreates the loop.
        raise AuthRedirect(next_path=safe_next_path(request.url.path))
    allow = settings.admin_allowlist
    if allow:
        normalized = (email or "").strip().lower()
        if not normalized or normalized not in allow:
            # Deny with a generic 403 for web; could redirect to a friendly page in the future.
            raise HTTPException(status_code=403, detail="No autorizado")
    return email or (user_id or "")


def log_in(request: Request, email: str) -> None:  # deprecated: no-op with Clerk
    request.session[SESSION_KEY] = email  # keep existing tests/overrides harmless


def log_out(request: Request) -> None:  # deprecated: no-op with Clerk
    request.session.pop(SESSION_KEY, None)
