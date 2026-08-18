"""FastAPI app factory. Mounts the JSON API routers and the server-rendered
review UI, and creates/seeds the DB schema on startup (idempotent)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import batches as batches_api
from app.api import config as config_api
from app.api import exports as exports_api
from app.api import records as records_api
from app.api import review as review_api
from app.config import BASE_DIR
from app.db import init_db
from app.web import routes as web_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Boletas Energía Mineral", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(batches_api.router)
app.include_router(records_api.router)
app.include_router(review_api.router)
app.include_router(exports_api.router)
app.include_router(config_api.router)
app.include_router(web_routes.router)

static_dir = BASE_DIR / "app" / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
