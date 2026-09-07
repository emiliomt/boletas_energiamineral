"""Server-rendered admin UI for configuración (catálogos): Proveedores y Transportistas."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import get_db
from app.models import Proveedor, Transportista

router = APIRouter(prefix="/admin/config", tags=["web-config"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


@router.get("")
def config_home(request: Request):
    return templates.TemplateResponse(request, "config_home.html", {})


# --- Proveedores --------------------------------------------------------------

@router.get("/proveedores")
def proveedores_list(request: Request, db: Session = Depends(get_db)):
    proveedores = db.query(Proveedor).order_by(Proveedor.name).all()
    return templates.TemplateResponse(request, "proveedores_list.html", {"proveedores": proveedores})


@router.post("/proveedores")
def proveedores_create(
    name: str = Form(...),
    origin: str = Form(""),
    precio_caja: str = Form(""),
    precio_transporte: str = Form(""),
    db: Session = Depends(get_db),
):
    def _num(value: str) -> float | None:
        v = (value or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    p = Proveedor(
        name=name.strip(),
        origin=origin.strip() or None,
        precio_caja=_num(precio_caja),
        precio_transporte=_num(precio_transporte),
        active=True,
    )
    db.add(p)
    db.commit()
    return RedirectResponse(url="/admin/config/proveedores", status_code=303)


@router.post("/proveedores/{proveedor_id}/update")
def proveedores_update(
    proveedor_id: int,
    name: str = Form(...),
    origin: str = Form(""),
    precio_caja: str = Form(""),
    precio_transporte: str = Form(""),
    active: bool = Form(False),
    db: Session = Depends(get_db),
):
    def _num(value: str) -> float | None:
        v = (value or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    p = db.get(Proveedor, proveedor_id)
    if p:
        p.name = name.strip()
        p.origin = origin.strip() or None
        p.precio_caja = _num(precio_caja)
        p.precio_transporte = _num(precio_transporte)
        p.active = bool(active)
        db.add(p)
        db.commit()
    return RedirectResponse(url="/admin/config/proveedores", status_code=303)


@router.post("/proveedores/{proveedor_id}/toggle")
def proveedores_toggle(proveedor_id: int, db: Session = Depends(get_db)):
    p = db.get(Proveedor, proveedor_id)
    if p:
        p.active = not p.active
        db.add(p)
        db.commit()
    return RedirectResponse(url="/admin/config/proveedores", status_code=303)


# --- Transportistas -----------------------------------------------------------

@router.get("/transportistas")
def transportistas_list(request: Request, db: Session = Depends(get_db)):
    transportistas = db.query(Transportista).order_by(Transportista.canonical_name).all()
    return templates.TemplateResponse(
        request, "transportistas_list.html", {"transportistas": transportistas}
    )


@router.post("/transportistas")
def transportistas_create(
    canonical_name: str = Form(...),
    phone: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    t = Transportista(
        canonical_name=canonical_name.strip(),
        phone=phone.strip() or None,
        notes=notes.strip() or None,
        active=True,
    )
    db.add(t)
    db.commit()
    return RedirectResponse(url="/admin/config/transportistas", status_code=303)


@router.post("/transportistas/{transportista_id}/update")
def transportistas_update(
    transportista_id: int,
    canonical_name: str = Form(...),
    phone: str = Form(""),
    notes: str = Form(""),
    active: bool = Form(False),
    db: Session = Depends(get_db),
):
    t = db.get(Transportista, transportista_id)
    if t:
        t.canonical_name = canonical_name.strip()
        t.phone = phone.strip() or None
        t.notes = notes.strip() or None
        t.active = bool(active)
        db.add(t)
        db.commit()
    return RedirectResponse(url="/admin/config/transportistas", status_code=303)


@router.post("/transportistas/{transportista_id}/toggle")
def transportistas_toggle(transportista_id: int, db: Session = Depends(get_db)):
    t = db.get(Transportista, transportista_id)
    if t:
        t.active = not t.active
        db.add(t)
        db.commit()
    return RedirectResponse(url="/admin/config/transportistas", status_code=303)

