"""Server-rendered admin UI for proveedores (producers) and their rate card:
precio por caja de carbón and precio de transporte."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import get_db
from app.models import Producer

router = APIRouter(prefix="/admin/proveedores", tags=["web-proveedores"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


def _parse_optional_mxn(raw: str) -> float | None:
    text = (raw or "").strip().replace("$", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    value = float(text)
    if value < 0:
        raise ValueError("El precio no puede ser negativo.")
    return value


def _list_context(db: Session, error: str | None = None) -> dict:
    producers = db.query(Producer).order_by(Producer.active.desc(), Producer.name).all()
    return {"producers": producers, "error": error}


@router.get("")
def list_proveedores_web(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "producers_list.html", _list_context(db))


@router.post("")
def create_proveedor_web(
    request: Request,
    name: str = Form(...),
    default_origin: str = Form(""),
    precio_caja_carbon: str = Form(""),
    precio_transporte: str = Form(""),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return templates.TemplateResponse(
            request, "producers_list.html", _list_context(db, "El nombre del proveedor es obligatorio.")
        )
    duplicate = (
        db.query(Producer).filter(func.lower(Producer.name) == name.lower()).one_or_none()
    )
    if duplicate:
        return templates.TemplateResponse(
            request,
            "producers_list.html",
            _list_context(db, f"Ya existe un proveedor llamado “{duplicate.name}”."),
        )
    try:
        caja = _parse_optional_mxn(precio_caja_carbon)
        transporte = _parse_optional_mxn(precio_transporte)
    except ValueError as exc:
        return templates.TemplateResponse(request, "producers_list.html", _list_context(db, str(exc)))

    origin = default_origin.strip() or name
    db.add(
        Producer(
            name=name,
            default_origin=origin,
            active=True,
            precio_caja_carbon=caja,
            precio_transporte=transporte,
        )
    )
    db.commit()
    return RedirectResponse(url="/admin/proveedores", status_code=303)


@router.post("/{producer_id}")
def update_proveedor_web(
    request: Request,
    producer_id: int,
    default_origin: str = Form(""),
    precio_caja_carbon: str = Form(""),
    precio_transporte: str = Form(""),
    db: Session = Depends(get_db),
):
    producer = db.get(Producer, producer_id)
    if producer is None:
        return RedirectResponse(url="/admin/proveedores", status_code=303)
    try:
        producer.precio_caja_carbon = _parse_optional_mxn(precio_caja_carbon)
        producer.precio_transporte = _parse_optional_mxn(precio_transporte)
    except ValueError as exc:
        return templates.TemplateResponse(request, "producers_list.html", _list_context(db, str(exc)))
    producer.default_origin = default_origin.strip() or producer.name
    db.commit()
    return RedirectResponse(url="/admin/proveedores", status_code=303)


@router.post("/{producer_id}/toggle")
def toggle_proveedor_web(producer_id: int, db: Session = Depends(get_db)):
    producer = db.get(Producer, producer_id)
    if producer is not None:
        producer.active = not producer.active
        db.commit()
    return RedirectResponse(url="/admin/proveedores", status_code=303)
