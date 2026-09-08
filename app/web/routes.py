"""Server-rendered review UI: plain HTML forms (no JS framework/CDN
dependency, so it works fully offline) backed by the same DB and services
the JSON API uses."""
from __future__ import annotations

import logging
import shutil

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR, settings
from app.db import get_db
from app.ingestion.storage import store_upload
from app.models import (
    Batch,
    Boleta,
    BoletaRecord,
    Folio,
    FolioBatch,
    Producer,
    ReviewAudit,
    Proveedor,
    Transportista,
)
from app.ocr.factory import get_ocr_adapter
from app.pipeline.orchestrator import process_boleta
from app.reporting.summary import build_batch_summary, build_overview
from app.review.service import apply_review
from app.schemas import ReviewCorrection

from app.web.exception_display import describe_exceptions, summarize_exceptions
from app.web.csrf import require_valid_csrf, csrf_token_value
from app.auth.session import current_admin, require_admin_web

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))
templates.env.globals["describe_exceptions"] = describe_exceptions
templates.env.globals["summarize_exceptions"] = summarize_exceptions
templates.env.globals["csrf_token"] = csrf_token_value
_ocr_adapter = get_ocr_adapter()
logger = logging.getLogger(__name__)


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    batches = db.query(Batch).filter(Batch.deleted_at.is_(None)).order_by(Batch.id.desc()).all()
    review_count = db.query(BoletaRecord).filter(BoletaRecord.status == "needs_review").count()
    total_count = db.query(BoletaRecord).count()
    # The "Nuevo lote" name is chosen from the registered folio batches
    # (Lotes de Folios) rather than typed freehand, so a scanning batch is
    # always tied to a lote that was actually issued/registered.
    folio_batches = db.query(FolioBatch).order_by(FolioBatch.id.desc()).all()
    # For the "Nuevo lote" kind=entrada path (Phase 2): the operator picks
    # the producer manually at upload time -- there's no auto-detection.
    # UX: the dropdown should reflect Configuración → Proveedores, not the
    # legacy CSV-loaded `Producer` table. Mirror Proveedor -> Producer and
    # surface only those entries here (ordered by proveedor name).
    proveedores = db.query(Proveedor).filter_by(active=True).order_by(Proveedor.name).all()
    existing_producers_by_name = {p.name: p for p in db.query(Producer).all()}
    producers: list[Producer] = []
    for prov in proveedores:
        p = existing_producers_by_name.get(prov.name)
        if p is None:
            p = Producer(name=prov.name, default_origin=(prov.origin or None), active=prov.active)
            db.add(p)
            db.flush()
            existing_producers_by_name[prov.name] = p
        else:
            changed = False
            new_origin = prov.origin or None
            if p.default_origin != new_origin:
                p.default_origin = new_origin
                changed = True
            if p.active != prov.active:
                p.active = prov.active
                changed = True
            if changed:
                db.flush()
        producers.append(p)
    # Transportistas catalog for the always-visible dropdown on the Nuevo lote form.
    transportistas = (
        db.query(Transportista).filter_by(active=True).order_by(Transportista.canonical_name).all()
    )
    flash_error = request.session.pop("flash_error", None)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "batches": batches,
            "review_count": review_count,
            "total_count": total_count,
            "folio_batches": folio_batches,
            "producers": producers,
            "transportistas": transportistas,
            "flash_error": flash_error,
        },
    )


@router.get("/dashboard")
def dashboard_overview(
    request: Request,
    # Strings (not int) so the filter form's empty "Todos los lotes" option
    # (value="") doesn't 422 on int parsing; parsed to int below when present.
    batch_id: str = "",
    status: str = "",
    fletero: str = "",
    db: Session = Depends(get_db),
):
    selected_batch_id = int(batch_id) if batch_id.strip().isdigit() else None
    overview = build_overview(db, batch_id=selected_batch_id, status=status or None, fletero=fletero or None)
    batches = db.query(Batch).filter(Batch.deleted_at.is_(None)).order_by(Batch.id.desc()).all()
    fleteros = [
        f for (f,) in db.query(BoletaRecord.fletero)
        .filter(BoletaRecord.fletero.isnot(None))
        .distinct()
        .order_by(BoletaRecord.fletero)
        .all()
    ]
    return templates.TemplateResponse(
        request,
        "dashboard_overview.html",
        {
            "overview": overview,
            "batches": batches,
            "fleteros": fleteros,
            "selected_batch_id": selected_batch_id,
            "selected_status": status or "",
            "selected_fletero": fletero or "",
        },
    )


@router.post("/batches")
def create_batch_web(
    request: Request,
    label: str = Form(...),
    created_by: str = Form(""),
    kind: str = Form("salida"),
    producer_id: str = Form(""),
    transportista_id: str = Form(""),
    csrf_token: str = Form(""),
    admin: str = Depends(require_admin_web),
    db: Session = Depends(get_db),
):
    require_valid_csrf(request, csrf_token)
    # Require transportista selection for both Entrada and Salida.
    if not transportista_id.strip().isdigit():
        request.session["flash_error"] = "Selecciona un transportista."
        return RedirectResponse(url="/#nuevo-lote-heading", status_code=303)
    # Require proveedor selection when creating an Entrada lote.
    if kind == "entrada" and not producer_id.strip().isdigit():
        request.session["flash_error"] = "Para lotes de Entrada, selecciona un proveedor."
        return RedirectResponse(url="/#nuevo-lote-heading", status_code=303)
    resolved_producer_id = int(producer_id) if kind == "entrada" else None
    batch = Batch(
        label=label,
        # Prefer authenticated identity when not provided; keep client-provided value for backward compatibility.
        created_by=((created_by or "").strip() or admin or current_admin(request) or None),
        kind=kind if kind == "entrada" else "salida",
        producer_id=resolved_producer_id,
        transportista_id=int(transportista_id),
    )
    db.add(batch)
    db.commit()
    return RedirectResponse(url=f"/batches/{batch.id}?new=1", status_code=303)


def _delete_batches(db: Session, ids: list[int]) -> None:
    """Delete or soft-delete scanning lotes depending on environment."""
    if not ids:
        return
    if settings.is_production:
        # Soft-delete in production: preserve Boletas, Records, and ReviewAudit for auditability.
        from datetime import datetime, timezone
        deleted_at = datetime.now(timezone.utc)
        db.query(Batch).filter(Batch.id.in_(ids)).update({Batch.deleted_at: deleted_at}, synchronize_session=False)
        db.expire_all()
    else:
        # Legacy hard-delete for local/test behavior parity with existing suite
        record_ids = [
            record_id
            for (record_id,) in (
                db.query(BoletaRecord.id)
                .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
                .filter(Boleta.batch_id.in_(ids))
                .all()
            )
        ]
        if record_ids:
            db.query(Folio).filter(Folio.boleta_record_id.in_(record_ids)).update(
                {Folio.boleta_record_id: None, Folio.status: "issued", Folio.scanned_at: None},
                synchronize_session=False,
            )
            db.query(BoletaRecord).filter(BoletaRecord.reconciled_with_record_id.in_(record_ids)).update(
                {BoletaRecord.reconciled_with_record_id: None},
                synchronize_session=False,
            )
            # Preserve ReviewAudit history in production; in local/test legacy path, keep as-is (delete).
            db.query(ReviewAudit).filter(ReviewAudit.boleta_record_id.in_(record_ids)).delete(
                synchronize_session=False
            )
            db.query(BoletaRecord).filter(BoletaRecord.id.in_(record_ids)).delete(
                synchronize_session=False
            )
        db.query(Boleta).filter(Boleta.batch_id.in_(ids)).delete(synchronize_session=False)
        db.query(Batch).filter(Batch.id.in_(ids)).delete(synchronize_session=False)
        db.expire_all()


@router.post("/batches/delete")
def delete_batches_web(
    request: Request, ids: list[int] = Form(default=[]), csrf_token: str = Form(""), db: Session = Depends(get_db)
):
    require_valid_csrf(request, csrf_token)
    if ids:
        try:
            _delete_batches(db, ids)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to delete lotes %s", ids)
            request.session["flash_error"] = (
                "No se pudieron borrar los lotes seleccionados. Inténtalo de nuevo."
            )
    return RedirectResponse(url="/", status_code=303)


@router.get("/batches/{batch_id}")
def batch_detail(request: Request, batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    records = (
        db.query(BoletaRecord)
        .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
        .filter(Boleta.batch_id == batch_id)
        .filter(BoletaRecord.reconciled_with_record_id.is_(None))  # see app/reporting/summary.py
        .order_by(BoletaRecord.id)
        .all()
    )
    summary = build_batch_summary(db, batch_id)
    return templates.TemplateResponse(
        request, "batch_detail.html", {"batch": batch, "records": records, "summary": summary}
    )


@router.post("/batches/{batch_id}/upload")
def upload_web(
    request: Request,
    batch_id: int,
    files: list[UploadFile] = [],  # noqa: B006 (FastAPI reconstructs this per-request)
    cfe_slip_files: list[UploadFile] = [],  # noqa: B006
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB
    require_valid_csrf(request, csrf_token)
    batch = db.get(Batch, batch_id)
    for upload, document_type in [(f, "boleta") for f in files] + [(f, "cfe_slip") for f in cfe_slip_files]:
        # Read in chunks and enforce a firm 50MB limit.
        total = 0
        chunks: list[bytes] = []
        while True:
            data = upload.file.read(1024 * 1024)
            if not data:
                break
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                request.session["flash_error"] = f"Archivo demasiado grande (>50MB): {upload.filename}"
                chunks = []
                break
            chunks.append(data)
        if not chunks:
            continue
        content = b"".join(chunks)
        boletas = store_upload(db, batch, upload.filename, content, upload.content_type or "", document_type)
        for boleta in boletas:
            process_boleta(db, boleta, _ocr_adapter)
    db.commit()
    return RedirectResponse(url=f"/batches/{batch_id}?uploaded=1", status_code=303)
    batch = db.get(Batch, batch_id)
    for upload, document_type in [(f, "boleta") for f in files] + [(f, "cfe_slip") for f in cfe_slip_files]:
        content = upload.file.read()
        if not content:
            continue
        boletas = store_upload(db, batch, upload.filename, content, upload.content_type or "", document_type)
        for boleta in boletas:
            process_boleta(db, boleta, _ocr_adapter)
    db.commit()
    return RedirectResponse(url=f"/batches/{batch_id}?uploaded=1", status_code=303)


@router.get("/review")
def review_queue_web(request: Request, db: Session = Depends(get_db)):
    records = (
        db.query(BoletaRecord)
        .filter(BoletaRecord.status == "needs_review")
        .filter(BoletaRecord.reconciled_with_record_id.is_(None))
        .order_by(BoletaRecord.id)
        .all()
    )
    return templates.TemplateResponse(request, "review_queue.html", {"records": records})


@router.get("/review/{record_id}")
def review_detail_web(request: Request, record_id: int, db: Session = Depends(get_db)):
    record = db.get(BoletaRecord, record_id)
    proveedores = db.query(Proveedor).filter_by(active=True).order_by(Proveedor.name).all()
    transportistas = db.query(Transportista).filter_by(active=True).order_by(Transportista.canonical_name).all()
    return templates.TemplateResponse(
        request,
        "review_detail.html",
        {
            "record": record,
            "proveedores_sugeridos": proveedores,
            "transportistas_sugeridos": transportistas,
        },
    )


def _num(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


@router.post("/review/{record_id}")
def review_submit_web(
    request: Request,
    record_id: int,
    action: str = Form("correct"),
    edited_by: str = Form(""),
    note: str = Form(""),
    folio: str = Form(""),
    date: str = Form(""),
    proveedor: str = Form(""),
    origin: str = Form(""),
    secondary_origin: str = Form(""),
    destination: str = Form(""),
    contract_number: str = Form(""),
    concesion_minera: str = Form(""),
    material: str = Form(""),
    fletero: str = Form(""),
    truck_box_number: str = Form(""),
    weight_declared: str = Form(""),
    weight: str = Form(""),
    representante_legal: str = Form(""),
    trip_type: str = Form(""),
    poder_calorifico_superior: str = Form(""),
    humedad_pct: str = Form(""),
    ceniza_pct: str = Form(""),
    azufre_pct: str = Form(""),
    fsi: str = Form(""),
    granulometria: str = Form(""),
    csrf_token: str = Form(""),
    admin: str = Depends(require_admin_web),
    db: Session = Depends(get_db),
):
    require_valid_csrf(request, csrf_token)
    record = db.get(BoletaRecord, record_id)
    correction = ReviewCorrection(
        action="approve" if action == "approve" else "correct",
        # Stamp editor from the authenticated session; ignore client field
        edited_by=(admin or current_admin(request) or None),
        note=note or None,
        folio=folio or None,
        date=date or None,
        proveedor=proveedor or None,
        origin=origin or None,
        secondary_origin=secondary_origin or None,
        destination=destination or None,
        contract_number=contract_number or None,
        concesion_minera=concesion_minera or None,
        material=material or None,
        fletero=fletero or None,
        truck_box_number=truck_box_number or None,
        weight_declared=_num(weight_declared),
        weight=_num(weight),
        representante_legal=representante_legal or None,
        trip_type=trip_type or None,
        poder_calorifico_superior=poder_calorifico_superior or None,
        humedad_pct=humedad_pct or None,
        ceniza_pct=ceniza_pct or None,
        azufre_pct=azufre_pct or None,
        fsi=fsi or None,
        granulometria=granulometria or None,
    )
    apply_review(db, record, correction)
    db.commit()
    return RedirectResponse(url="/review?ok=1", status_code=303)
