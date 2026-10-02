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
- Clerk session tokens usually omit email. When ``ADMIN_EMAILS`` is set,
  the primary email is loaded from the Clerk Backend API so a real admin
  session is not rejected. A browser that is still not allowed is redirected
  to ``/login`` (HTML). API clients keep JSON 401/403.

No server-managed username/password login remains; the previous Supabase
form is removed in favor of Clerk. When Clerk is not configured, protected
routes fail closed (401 for API, redirect to /login for web)."""
from __future__ import annotations

import logging
import re
import time

import httpx
from fastapi import HTTPException, Request
from app.auth.clerk_handshake import (
    AuthHandshake,
    HandshakeAction,
    maybe_handshake,
    resolve_handshake_return,
    safe_next_path,
)
from app.config import settings

logger = logging.getLogger(__name__)

_USER_ID = re.compile(r"^user_[A-Za-z0-9]+$")
# user_id -> (expires_at, email or None). Avoid a Clerk round-trip per request.
_EMAIL_CACHE: dict[str, tuple[float, str | None]] = {}
_EMAIL_CACHE_TTL = 300.0
_EMAIL_CACHE_MISS_TTL = 30.0

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
    """Raised by require_admin_web when the browser should see /login.

    ``app/main.py`` turns this into a redirect. ``denied=True`` means the
    Clerk session was valid but the allowlist rejected it, so the login page
    must not bounce that browser straight back (that is the redirect loop).
    """

    def __init__(self, next_path: str, denied: bool = False):
        self.next_path = next_path
        self.denied = denied


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

def _extract_primary_email(payload: object) -> str | None:
    """Primary verified email from a Clerk user payload, if present."""
    if not isinstance(payload, dict):
        return None
    emails = payload.get("email_addresses")
    if not isinstance(emails, list):
        return None
    primary_id = payload.get("primary_email_address_id")

    def usable(item: object) -> str | None:
        if not isinstance(item, dict):
            return None
        addr = item.get("email_address")
        if not isinstance(addr, str) or not addr.strip():
            return None
        verification = item.get("verification")
        if isinstance(verification, dict) and verification.get("status") not in (None, "verified"):
            return None
        return addr.strip()

    if primary_id:
        for item in emails:
            if isinstance(item, dict) and item.get("id") == primary_id:
                return usable(item)
    for item in emails:
        found = usable(item)
        if found:
            return found
    return None


def _lookup_clerk_email(user_id: str) -> str | None:
    """Load the user's primary email. Session JWTs do not include it."""
    if not _USER_ID.fullmatch(user_id):
        return None
    now = time.monotonic()
    cached = _EMAIL_CACHE.get(user_id)
    if cached is not None and cached[0] > now:
        return cached[1]
    secret = (settings.clerk_secret_key or "").strip()
    if not secret:
        return None
    email: str | None = None
    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
            timeout=5.0,
        )
        response.raise_for_status()
        email = _extract_primary_email(response.json())
    except Exception:
        logger.exception("Clerk user email lookup failed")
        _EMAIL_CACHE[user_id] = (now + _EMAIL_CACHE_MISS_TTL, None)
        return None
    _EMAIL_CACHE[user_id] = (now + _EMAIL_CACHE_TTL, email)
    return email


def _resolve_allowlist_email(email: str | None, user_id: str | None) -> str | None:
    if email or not settings.admin_allowlist or not user_id:
        return email
    return _lookup_clerk_email(user_id)


def _email_allowed(email: str | None) -> bool:
    allow = settings.admin_allowlist
    if not allow:
        return True
    normalized = (email or "").strip().lower()
    return bool(normalized) and normalized in allow


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
    email = _resolve_allowlist_email(email, user_id)
    if not _email_allowed(email):
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
    email = _resolve_allowlist_email(email, user_id)
    if not _email_allowed(email):
        # HTML, not {"detail":"No autorizado"}. denied=1 stops clerk-js from
        # sending this already-signed-in browser straight back to the app.
        raise AuthRedirect(next_path=safe_next_path(request.url.path), denied=True)
    return email or (user_id or "")


def log_in(request: Request, email: str) -> None:  # deprecated: no-op with Clerk
    request.session[SESSION_KEY] = email  # keep existing tests/overrides harmless


def log_out(request: Request) -> None:  # deprecated: no-op with Clerk
    request.session.pop(SESSION_KEY, None)
