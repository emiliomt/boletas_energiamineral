"""FastAPI-owned session (signed cookie via Starlette's SessionMiddleware)
backed by Supabase Auth for credential verification. Chosen over a
client-side Supabase JS SDK / token-in-localStorage pattern because this
app is server-rendered Jinja2, not a SPA -- the backend verifies
credentials against Supabase once at login, then owns its own session from
there, same shape as any traditional server-rendered app's auth.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

SESSION_KEY = "admin_email"


class AuthRedirect(Exception):
    """Raised by require_admin_web when unauthenticated; app/main.py
    registers an exception handler that turns this into a redirect to
    /login?next=<path>."""

    def __init__(self, next_path: str):
        self.next_path = next_path


def current_admin(request: Request) -> str | None:
    return request.session.get(SESSION_KEY)


def require_admin_api(request: Request) -> str:
    admin = current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return admin


def require_admin_web(request: Request) -> str:
    admin = current_admin(request)
    if not admin:
        raise AuthRedirect(next_path=request.url.path)
    return admin


def log_in(request: Request, email: str) -> None:
    request.session[SESSION_KEY] = email


def log_out(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)
