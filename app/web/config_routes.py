"""Server-rendered admin UI for Configuración: Proveedores y Transportistas.

CRUD pages with simple forms. Suggestions feed datalists elsewhere."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import get_db
from app.models import Proveedor, Transportista, Producer
from app.web.csrf import csrf_token_value, require_valid_csrf

router = APIRouter(prefix="/admin/config", tags=["web-config"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))
templates.env.globals["csrf_token"] = csrf_token_value


@router.get("")
def config_hub(request: Request):
    return templates.TemplateResponse(request, "config_hub.html", {})


# --- Proveedores -------------------------------------------------------------
@router.get("/proveedores")
def list_proveedores_web(request: Request, db: Session = Depends(get_db)):
    proveedores = db.query(Proveedor).order_by(Proveedor.active.desc(), Proveedor.name).all()
    return templates.TemplateResponse(
        request,
        "config_proveedores.html",
        {"proveedores": proveedores},
    )


@router.post("/proveedores")
def create_or_update_proveedor_web(
    request: Request,
    name: str = Form(...),
    origin: str = Form(""),
    precio_caja: str = Form(""),
    precio_peso: str = Form(""),
    precio_transporte: str = Form(""),
    modo_pago: str = Form("flete"),
    proveedor_id: str = Form(""),
    active: str = Form("1"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_valid_csrf(request, csrf_token)  # token check (session-backed token)
    def _sync_proveedor_to_producer(prov: Proveedor) -> None:
        """Ensure the Entrada `Producer` catalog mirrors Configuración → Proveedores.

        - Create a Producer row if missing (name is the natural key)
        - Keep `active` and `default_origin` in sync (do not touch format_id)
        """
        existing = db.query(Producer).filter_by(name=prov.name).one_or_none()
        if existing is None:
            db.add(
                Producer(
                    name=prov.name,
                    default_origin=(prov.origin or None),
                    active=bool(prov.active),
                )
            )
            db.flush()
        else:
            changed = False
            new_origin = prov.origin or None
            if existing.default_origin != new_origin:
                existing.default_origin = new_origin
                changed = True
            if existing.active != bool(prov.active):
                existing.active = bool(prov.active)
                changed = True
            if changed:
                db.flush()

    def _num(value: str) -> float | None:
        v = (value or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    is_active = active == "1"
    pid = int(proveedor_id) if proveedor_id and proveedor_id.isdigit() else None
    if pid:
        p = db.get(Proveedor, pid)
        if p:
            p.name = name.strip()
            p.origin = origin.strip() or None
            p.precio_caja = _num(precio_caja)
            p.precio_peso = _num(precio_peso)
            p.precio_transporte = _num(precio_transporte)
            p.modo_pago = (modo_pago or "flete").strip()
            p.active = is_active
            try:
                _sync_proveedor_to_producer(p)
                db.commit()
            except IntegrityError:
                db.rollback()
        return RedirectResponse(url="/admin/config/proveedores?ok=1", status_code=303)
    else:
        p = Proveedor(
            name=name.strip(),
            origin=origin.strip() or None,
            precio_caja=_num(precio_caja),
            precio_peso=_num(precio_peso),
            precio_transporte=_num(precio_transporte),
            modo_pago=(modo_pago or "flete").strip(),
            active=is_active,
        )
        db.add(p)
        try:
            db.flush()  # have an id for sync
            _sync_proveedor_to_producer(p)
            db.commit()
        except IntegrityError:
            db.rollback()
        return RedirectResponse(url="/admin/config/proveedores?ok=1", status_code=303)


@router.post("/proveedores/{proveedor_id}/toggle")
def toggle_proveedor_active_web(
    request: Request, proveedor_id: int, csrf_token: str = Form(""), db: Session = Depends(get_db)
):
    # CSRF check (validate when enforced)
    require_valid_csrf(request, csrf_token)
    p = db.get(Proveedor, proveedor_id)
    if p:
        p.active = not p.active
        # Keep Producer.active mirrored too if it exists
        existing = db.query(Producer).filter_by(name=p.name).one_or_none()
        if existing is not None:
            existing.active = p.active
        db.commit()
    return RedirectResponse(url="/admin/config/proveedores?ok=1", status_code=303)


# --- Transportistas ----------------------------------------------------------
@router.get("/transportistas")
def list_transportistas_web(request: Request, db: Session = Depends(get_db)):
    transportistas = db.query(Transportista).order_by(Transportista.active.desc(), Transportista.canonical_name).all()
    return templates.TemplateResponse(
        request,
        "config_transportistas.html",
        {"transportistas": transportistas},
    )


@router.post("/transportistas")
def create_or_update_transportista_web(
    request: Request,
    canonical_name: str = Form(...),
    phone: str = Form(""),
    notes: str = Form(""),
    transportista_id: str = Form(""),
    active: str = Form("1"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_valid_csrf(request, csrf_token)
    tid = int(transportista_id) if transportista_id and transportista_id.isdigit() else None
    is_active = active == "1"
    if tid:
        t = db.get(Transportista, tid)
        if t:
            t.canonical_name = canonical_name.strip()
            t.phone = phone.strip() or None
            t.notes = notes.strip() or None
            t.active = is_active
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
        return RedirectResponse(url="/admin/config/transportistas?ok=1", status_code=303)
    else:
        t = Transportista(
            canonical_name=canonical_name.strip(),
            phone=phone.strip() or None,
            notes=notes.strip() or None,
            active=is_active,
        )
        db.add(t)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        return RedirectResponse(url="/admin/config/transportistas?ok=1", status_code=303)


@router.post("/transportistas/{transportista_id}/toggle")
def toggle_transportista_active_web(
    request: Request, transportista_id: int, csrf_token: str = Form(""), db: Session = Depends(get_db)
):
    require_valid_csrf(request, csrf_token)
    t = db.get(Transportista, transportista_id)
    if t:
        t.active = not t.active
        db.commit()
    return RedirectResponse(url="/admin/config/transportistas?ok=1", status_code=303)

