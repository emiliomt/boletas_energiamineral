"""Login/logout for the single admin account. No signup route -- the
admin is created out-of-band via scripts/create_admin_user.py."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.session import log_in, log_out
from app.auth.supabase_auth import SupabaseNotConfigured, verify_credentials
from app.config import BASE_DIR, settings
from app.web.csrf import csrf_token_value, require_valid_csrf

router = APIRouter(tags=["web-auth"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))
templates.env.globals["csrf_token"] = csrf_token_value


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    # CSRF protection for cookie-authenticated form posts
    require_valid_csrf(request, csrf_token)
    try:
        result = verify_credentials(email, password)
    except SupabaseNotConfigured as exc:
        return templates.TemplateResponse(request, "login.html", {"next": next, "error": str(exc)})

    if result is None:
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Correo o contraseña incorrectos."}
        )

    # Admin allowlist (optional): only allow configured emails
    allow = settings.admin_allowlist
    normalized_email = (email or "").strip().lower()
    if allow and normalized_email not in allow:
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Tu cuenta no tiene acceso de administrador."}
        )

    log_in(request, email)
    # Open-redirect protection: only allow relative in-site paths starting with a single "/"
    safe_next = next if isinstance(next, str) and next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(url=safe_next or "/", status_code=303)


@router.post("/logout")
def logout_submit(request: Request, csrf_token: str = Form("")):
    require_valid_csrf(request, csrf_token)
    log_out(request)
    return RedirectResponse(url="/login", status_code=303)
