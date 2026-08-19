"""Server-rendered review UI: plain HTML forms (no JS framework/CDN
dependency, so it works fully offline) backed by the same DB and services
the JSON API uses."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import get_db
from app.ingestion.storage import store_upload
from app.models import Batch, Boleta, BoletaRecord, FolioBatch
from app.ocr.tesseract_adapter import TesseractOCRAdapter
from app.pipeline.orchestrator import process_boleta
from app.reporting.summary import build_batch_summary
from app.review.service import apply_review
from app.schemas import ReviewCorrection

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))
_ocr_adapter = TesseractOCRAdapter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    batches = db.query(Batch).order_by(Batch.id.desc()).all()
    review_count = db.query(BoletaRecord).filter(BoletaRecord.status == "needs_review").count()
    total_count = db.query(BoletaRecord).count()
    # The "Nuevo lote" name is chosen from the registered folio batches
    # (Lotes de Folios) rather than typed freehand, so a scanning batch is
    # always tied to a lote that was actually issued/registered.
    folio_batches = db.query(FolioBatch).order_by(FolioBatch.id.desc()).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "batches": batches,
            "review_count": review_count,
            "total_count": total_count,
            "folio_batches": folio_batches,
        },
    )


@router.post("/batches")
def create_batch_web(label: str = Form(...), created_by: str = Form(""), db: Session = Depends(get_db)):
    batch = Batch(label=label, created_by=created_by or None)
    db.add(batch)
    db.commit()
    return RedirectResponse(url=f"/batches/{batch.id}", status_code=303)


@router.get("/batches/{batch_id}")
def batch_detail(request: Request, batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    records = (
        db.query(BoletaRecord)
        .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
        .filter(Boleta.batch_id == batch_id)
        .order_by(BoletaRecord.id)
        .all()
    )
    summary = build_batch_summary(db, batch_id)
    return templates.TemplateResponse(
        request, "batch_detail.html", {"batch": batch, "records": records, "summary": summary}
    )


@router.post("/batches/{batch_id}/upload")
def upload_web(batch_id: int, files: list[UploadFile], db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    for upload in files:
        content = upload.file.read()
        if not content:
            continue
        boletas = store_upload(db, batch, upload.filename, content, upload.content_type or "")
        for boleta in boletas:
            process_boleta(db, boleta, _ocr_adapter)
    db.commit()
    return RedirectResponse(url=f"/batches/{batch_id}", status_code=303)


@router.get("/review")
def review_queue_web(request: Request, db: Session = Depends(get_db)):
    records = db.query(BoletaRecord).filter(BoletaRecord.status == "needs_review").order_by(BoletaRecord.id).all()
    return templates.TemplateResponse(request, "review_queue.html", {"records": records})


@router.get("/review/{record_id}")
def review_detail_web(request: Request, record_id: int, db: Session = Depends(get_db)):
    record = db.get(BoletaRecord, record_id)
    return templates.TemplateResponse(request, "review_detail.html", {"record": record})


@router.post("/review/{record_id}")
def review_submit_web(
    request: Request,
    record_id: int,
    action: str = Form("correct"),
    edited_by: str = Form(""),
    note: str = Form(""),
    folio: str = Form(""),
    date: str = Form(""),
    origin: str = Form(""),
    destination: str = Form(""),
    material: str = Form(""),
    fletero: str = Form(""),
    weight: str = Form(""),
    trip_type: str = Form(""),
    db: Session = Depends(get_db),
):
    record = db.get(BoletaRecord, record_id)
    correction = ReviewCorrection(
        action="approve" if action == "approve" else "correct",
        edited_by=edited_by or None,
        note=note or None,
        folio=folio or None,
        date=date or None,
        origin=origin or None,
        destination=destination or None,
        material=material or None,
        fletero=fletero or None,
        weight=float(weight) if weight.strip() else None,
        trip_type=trip_type or None,
    )
    apply_review(db, record, correction)
    db.commit()
    return RedirectResponse(url="/review", status_code=303)
