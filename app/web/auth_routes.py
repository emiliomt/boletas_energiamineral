"""Login/logout UI entry points.

With Clerk-based authentication, the login page renders the Clerk SignIn
component (client-side). There is no server-side username/password form.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.session import log_out
from app.config import BASE_DIR, settings
from app.web.csrf import csrf_token_value, require_valid_csrf

router = APIRouter(tags=["web-auth"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))
templates.env.globals["csrf_token"] = csrf_token_value
templates.env.globals["settings"] = settings


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    # Pass Clerk publishable key to the template for client-side initialization.
    # Open-redirect protection: only allow relative in-site paths starting with a single "/"
    safe_next = next if isinstance(next, str) and next.startswith("/") and not next.startswith("//") else "/"
    context = {
        "next": safe_next,
        "clerk_publishable_key": settings.clerk_publishable_key,
        "clerk_configured": bool(settings.clerk_publishable_key),
        "error": None,
    }
    return templates.TemplateResponse(request, "login.html", context)


@router.post("/login")
def login_submit(
    request: Request,
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    # Legacy endpoint kept to avoid breaking clients; no server-side login.
    require_valid_csrf(request, csrf_token)
    # Always redirect back to /login; Clerk handles sign-in via JS.
    safe_next = next if isinstance(next, str) and next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(url=f"/login?next={safe_next or '/'}", status_code=303)


@router.post("/logout")
def logout_submit(request: Request, csrf_token: str = Form("")):
    require_valid_csrf(request, csrf_token)
    log_out(request)
    return RedirectResponse(url="/login", status_code=303)
