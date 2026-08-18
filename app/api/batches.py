"""Batch endpoints: create a batch, list batches, upload boletas into a
batch (running the full pipeline per file), and get a batch's summary."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.storage import store_upload
from app.models import Batch
from app.ocr.tesseract_adapter import TesseractOCRAdapter
from app.pipeline.orchestrator import process_boleta
from app.reporting.summary import build_batch_summary
from app.schemas import BatchCreate, BatchOut, BatchSummary

router = APIRouter(prefix="/api/batches", tags=["batches"])

_ocr_adapter = TesseractOCRAdapter()


@router.post("", response_model=BatchOut)
def create_batch(payload: BatchCreate, db: Session = Depends(get_db)) -> Batch:
    batch = Batch(label=payload.label, created_by=payload.created_by, notes=payload.notes)
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db)) -> list[Batch]:
    return db.query(Batch).order_by(Batch.id.desc()).all()


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, db: Session = Depends(get_db)) -> Batch:
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/{batch_id}/upload")
def upload_to_batch(batch_id: int, files: list[UploadFile], db: Session = Depends(get_db)) -> dict:
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    processed = []
    for upload in files:
        content = upload.file.read()
        boletas = store_upload(db, batch, upload.filename, content, upload.content_type or "")
        for boleta in boletas:
            record = process_boleta(db, boleta, _ocr_adapter)
            processed.append({"boleta_id": boleta.id, "record_id": record.id, "status": record.status})
    db.commit()
    return {"batch_id": batch_id, "processed": processed, "count": len(processed)}


@router.get("/{batch_id}/summary", response_model=BatchSummary)
def batch_summary(batch_id: int, db: Session = Depends(get_db)) -> BatchSummary:
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return build_batch_summary(db, batch_id)
