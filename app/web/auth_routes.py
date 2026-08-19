"""Login/logout for the single admin account. No signup route -- the
admin is created out-of-band via scripts/create_admin_user.py."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.session import log_in, log_out
from app.auth.supabase_auth import SupabaseNotConfigured, verify_credentials
from app.config import BASE_DIR

router = APIRouter(tags=["web-auth"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    try:
        result = verify_credentials(email, password)
    except SupabaseNotConfigured as exc:
        return templates.TemplateResponse(request, "login.html", {"next": next, "error": str(exc)})

    if result is None:
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Correo o contraseña incorrectos."}
        )

    log_in(request, email)
    return RedirectResponse(url=next or "/", status_code=303)


@router.post("/logout")
def logout_submit(request: Request):
    log_out(request)
    return RedirectResponse(url="/login", status_code=303)
