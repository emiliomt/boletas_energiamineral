from __future__ import annotations

import hmac
import secrets
from fastapi import HTTPException, Request
from app.config import settings

SESSION_CSRF_KEY = "_csrf_token"


def csrf_token_value(request: Request) -> str:
    """Get or create a CSRF token bound to the user's session."""
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def require_valid_csrf(request: Request, token_from_form: str | None) -> None:
    # Only enforce CSRF when running in production-like environments.
    if not settings.is_production:
        return
    expected = request.session.get(SESSION_CSRF_KEY)
    # If the session has no token yet, generate one now and reject the request.
    if not expected:
        request.session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)
        raise HTTPException(status_code=403, detail="CSRF token missing.")
    if not token_from_form or not hmac.compare_digest(str(token_from_form), str(expected)):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

