"""Authentication gate for web and API routes.

Reworked to rely on Clerk sessions:
- The frontend renders Clerk's SignIn/UserButton UI.
- The backend verifies the Clerk session token on each request via
  `clerk_backend_api.security.authenticate_request`, accepting a token from
  the `Authorization: Bearer` header or the `__session` cookie.

No server-managed username/password login remains; the previous Supabase
form is removed in favor of Clerk. When Clerk is not configured, protected
routes fail closed (401 for API, redirect to /login for web)."""
from __future__ import annotations

from fastapi import HTTPException, Request
from app.config import settings

try:
    # Provided by `clerk-backend-api`
    from clerk_backend_api.security import authenticate_request
    from clerk_backend_api.security.types import AuthenticateRequestOptions
except Exception:  # pragma: no cover - import failures exercised in tests via missing deps
    authenticate_request = None  # type: ignore[assignment]
    AuthenticateRequestOptions = None  # type: ignore[assignment]


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


def _verify_clerk_request(request: Request) -> tuple[bool, str | None, str | None, str | None]:
    """Returns (is_authenticated, user_id, email, reason)."""
    if authenticate_request is None or AuthenticateRequestOptions is None:
        return (False, None, None, "clerk_sdk_missing")
    if not _is_clerk_configured():
        return (False, None, None, "clerk_not_configured")
    opts = AuthenticateRequestOptions(
        secret_key=settings.clerk_secret_key,
        jwt_key=settings.clerk_jwt_key,
        authorized_parties=settings.clerk_authorized_parties_list or None,
        accepts_token=["session_token"],  # restrict to end-user session tokens
    )
    state = authenticate_request(request, opts)
    if not getattr(state, "is_signed_in", False) and not getattr(state, "is_authenticated", False):
        reason = getattr(state, "reason", None)
        rname = getattr(reason, "name", None) if reason is not None else "unauthorized"
        return (False, None, None, rname or "unauthorized")
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


def require_admin_web(request: Request) -> str:
    ok, user_id, email, _ = _verify_clerk_request(request)
    if not ok:
        # Redirect to login preserving the in-site path only (open-redirect safe).
        next_path = request.url.path
        if not isinstance(next_path, str) or not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        raise AuthRedirect(next_path=next_path)
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
