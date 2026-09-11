"""FastAPI app factory. Mounts the JSON API routers and the server-rendered
review UI, and creates/seeds the DB schema on startup (idempotent)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import os

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import batches as batches_api
from app.api import config as config_api
from app.api import exports as exports_api
from app.api import folio_batches as folio_batches_api
from app.api import records as records_api
from app.api import review as review_api
from app.auth.session import AuthRedirect, require_admin_api, require_admin_web
from app.config import BASE_DIR, settings
from app.db import init_db
from app.web import auth_routes
from app.web import folio_batches_routes as folio_batches_web
from app.web import routes as web_routes
from app.web import config_routes as config_web


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    # Log once whether Clerk keys are set (boolean only; never log secrets)
    logger = logging.getLogger(__name__)
    pk = (settings.clerk_publishable_key or os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip()
    sk = (settings.clerk_secret_key or os.environ.get("CLERK_SECRET_KEY") or "").strip()
    logger.info("Clerk publishable key set: %s", bool(pk))
    logger.info("Clerk secret key set: %s", bool(sk))
    yield


app = FastAPI(title="Boletas Energía Mineral", version="0.1.0", lifespan=lifespan)
# Fail closed on insecure session secret in production-like environments.
if settings.is_production and settings.session_secret_key == "dev-only-insecure-secret-change-me":
    raise ValueError(
        "SESSION_SECRET_KEY must be set to a strong random value in production."
        " Refusing to start with the insecure placeholder."
    )
# Harden session cookie defaults in production, keep dev-friendly locally.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    https_only=True if settings.is_production else False,
    same_site="lax",
)


@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={exc.next_path}", status_code=303)


@app.get("/api/health")
def health() -> dict:
    pk = (settings.clerk_publishable_key or os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip()
    sk = (settings.clerk_secret_key or os.environ.get("CLERK_SECRET_KEY") or "").strip()
    return {
        "status": "ok",
        "clerk_publishable_key_set": bool(pk),
        "clerk_secret_key_set": bool(sk),
    }


# /login, /logout, and /api/health are the only unauthenticated routes --
# every other route (including the pre-existing OCR-flow ones) requires the
# single admin session, applied here via `dependencies=` rather than
# touching each router file individually.
app.include_router(auth_routes.router)

_api_auth = [Depends(require_admin_api)]
app.include_router(batches_api.router, dependencies=_api_auth)
app.include_router(records_api.router, dependencies=_api_auth)
app.include_router(review_api.router, dependencies=_api_auth)
app.include_router(exports_api.router, dependencies=_api_auth)
app.include_router(config_api.router, dependencies=_api_auth)
app.include_router(folio_batches_api.router, dependencies=_api_auth)

_web_auth = [Depends(require_admin_web)]
app.include_router(folio_batches_web.router, dependencies=_web_auth)
app.include_router(config_web.router, dependencies=_web_auth)
app.include_router(web_routes.router, dependencies=_web_auth)

static_dir = BASE_DIR / "app" / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
