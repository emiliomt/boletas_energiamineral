"""Folio batch endpoints: generate a batch of folios (+ QR codes) to hand
to the print vendor, and download the print-ready PDF or a plain CSV."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.exports.folio_batch_export import build_folio_batch_csv
from app.models import Folio, FolioBatch
from app.qr.batch_pdf import generate_batch_pdf
from app.qr.generator import qr_payload_for_folio
from app.schemas import FolioBatchCreate, FolioBatchDetail, FolioBatchOut

router = APIRouter(prefix="/api/folio-batches", tags=["folio-batches"])


def _status_counts(db: Session, folio_batch_id: int) -> dict[str, int]:
    rows = db.query(Folio.status).filter_by(folio_batch_id=folio_batch_id).all()
    counts = {"issued": 0, "scanned": 0, "void": 0}
    for (status,) in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _to_detail(db: Session, batch: FolioBatch) -> FolioBatchDetail:
    counts = _status_counts(db, batch.id)
    return FolioBatchDetail(
        id=batch.id,
        label=batch.label,
        mode=batch.mode,
        count=batch.count,
        vendor=batch.vendor,
        notes=batch.notes,
        created_by=batch.created_by,
        created_at=batch.created_at,
        issued_count=counts["issued"],
        scanned_count=counts["scanned"],
        void_count=counts["void"],
    )


@router.post("", response_model=FolioBatchOut)
def create_folio_batch(payload: FolioBatchCreate, db: Session = Depends(get_db)) -> FolioBatch:
    if payload.mode == "sequential":
        folio_values = [f"{payload.prefix}{n}" for n in range(payload.start_number, payload.start_number + payload.count)]
    else:
        # Dedupe while preserving order; reject if the pasted list itself has duplicates.
        seen: set[str] = set()
        folio_values = []
        for f in payload.folios:
            f = f.strip()
            if not f:
                continue
            if f in seen:
                raise HTTPException(status_code=400, detail=f"Duplicate folio in the pasted list: {f}")
            seen.add(f)
            folio_values.append(f)
        if not folio_values:
            raise HTTPException(status_code=400, detail="No valid folios in the pasted list")

    existing = db.query(Folio.folio).filter(Folio.folio.in_(folio_values)).all()
    if existing:
        collided = ", ".join(f for (f,) in existing[:10])
        raise HTTPException(status_code=409, detail=f"Folio(s) already exist: {collided}")

    batch = FolioBatch(
        label=payload.label,
        mode=payload.mode,
        prefix=payload.prefix,
        start_number=payload.start_number,
        count=len(folio_values),
        vendor=payload.vendor,
        notes=payload.notes,
        created_by=payload.created_by,
    )
    db.add(batch)
    db.flush()

    for folio_value in folio_values:
        db.add(Folio(folio_batch_id=batch.id, folio=folio_value, qr_payload=qr_payload_for_folio(folio_value)))

    db.commit()
    db.refresh(batch)
    return batch


@router.get("", response_model=list[FolioBatchDetail])
def list_folio_batches(db: Session = Depends(get_db)) -> list[FolioBatchDetail]:
    batches = db.query(FolioBatch).order_by(FolioBatch.id.desc()).all()
    return [_to_detail(db, b) for b in batches]


@router.get("/{folio_batch_id}", response_model=FolioBatchDetail)
def get_folio_batch(folio_batch_id: int, db: Session = Depends(get_db)) -> FolioBatchDetail:
    batch = db.get(FolioBatch, folio_batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Folio batch not found")
    return _to_detail(db, batch)


@router.get("/{folio_batch_id}/print-pdf")
def download_print_pdf(folio_batch_id: int, db: Session = Depends(get_db)) -> Response:
    batch = db.get(FolioBatch, folio_batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Folio batch not found")
    folios = db.query(Folio).filter_by(folio_batch_id=folio_batch_id).order_by(Folio.id).all()
    pdf_bytes = generate_batch_pdf(batch, folios)
    filename = f"boletas_{batch.label.replace(' ', '_')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{folio_batch_id}/export-csv")
def download_export_csv(folio_batch_id: int, db: Session = Depends(get_db)) -> PlainTextResponse:
    batch = db.get(FolioBatch, folio_batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Folio batch not found")
    folios = db.query(Folio).filter_by(folio_batch_id=folio_batch_id).order_by(Folio.id).all()
    return PlainTextResponse(build_folio_batch_csv(folios), media_type="text/csv")
