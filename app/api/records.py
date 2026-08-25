"""Record read endpoints: list/filter records, get one record's full detail
(incl. exceptions, matched rules, OCR text), and serve the original scan
image for audit purposes. Also exposes the review queue."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Boleta, BoletaRecord
from app.schemas import BoletaRecordDetail

router = APIRouter(prefix="/api", tags=["records"])


def _to_detail(record: BoletaRecord) -> BoletaRecordDetail:
    return BoletaRecordDetail(
        record_id=record.id,
        boleta_id_internal=record.boleta_id,
        kind=record.kind,
        producer_id=record.producer_id,
        salida_status=record.salida_status,
        cfe_entry_weight=record.cfe_entry_weight,
        cfe_exit_weight=record.cfe_exit_weight,
        delivered_weight=record.delivered_weight,
        boleta_id=record.folio,
        date=record.date,
        origin=record.origin,
        destination=record.destination,
        material=record.material,
        fletero=record.fletero,
        weight=record.weight,
        weight_declared=record.weight_declared,
        weight_source=record.weight_source,
        trip_type=record.trip_type,
        tariff_amount=record.tariff_amount,
        inventory_direction=record.inventory_direction,
        inventory_quantity=record.inventory_quantity,
        confidence_score=record.confidence_score,
        status=record.status,
        exceptions=record.exceptions or [],
        ocr_text=record.ocr_text,
        ocr_confidence=record.ocr_confidence,
        ocr_engine=record.ocr_engine,
        secondary_origin=record.secondary_origin,
        contract_number=record.contract_number,
        truck_box_number=record.truck_box_number,
        proveedor=record.proveedor,
        concesion_minera=record.concesion_minera,
        representante_legal=record.representante_legal,
        quality_data=record.quality_data or {},
        matched_route_rule_id=record.matched_route_rule_id,
        matched_tariff_rule_id=record.matched_tariff_rule_id,
        matched_weight_rule_id=record.matched_weight_rule_id,
        field_confidences=record.field_confidences or {},
        image_url=f"/api/records/{record.id}/image",
    )


@router.get("/records", response_model=list[BoletaRecordDetail])
def list_records(
    batch_id: int | None = None,
    status: str | None = None,
    fletero: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
) -> list[BoletaRecordDetail]:
    query = (
        db.query(BoletaRecord)
        .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
        # Phase 3: exclude Salida documents merged into another record's
        # reconciliation -- see app/reporting/summary.py::build_overview.
        .filter(BoletaRecord.reconciled_with_record_id.is_(None))
    )
    if batch_id is not None:
        query = query.filter(Boleta.batch_id == batch_id)
    if status is not None:
        query = query.filter(BoletaRecord.status == status)
    if fletero is not None:
        query = query.filter(BoletaRecord.fletero == fletero)
    if date_from is not None:
        query = query.filter(BoletaRecord.date >= date_from)
    if date_to is not None:
        query = query.filter(BoletaRecord.date <= date_to)
    return [_to_detail(r) for r in query.order_by(BoletaRecord.id.desc()).all()]


@router.get("/records/{record_id}", response_model=BoletaRecordDetail)
def get_record(record_id: int, db: Session = Depends(get_db)) -> BoletaRecordDetail:
    record = db.get(BoletaRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return _to_detail(record)


@router.get("/records/{record_id}/image")
def get_record_image(record_id: int, db: Session = Depends(get_db)) -> FileResponse:
    record = db.get(BoletaRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    boleta = db.get(Boleta, record.boleta_id)
    if not boleta:
        raise HTTPException(status_code=404, detail="Original scan not found")
    return FileResponse(boleta.stored_path, media_type=boleta.mime_type)


@router.get("/review-queue", response_model=list[BoletaRecordDetail])
def review_queue(db: Session = Depends(get_db)) -> list[BoletaRecordDetail]:
    records = (
        db.query(BoletaRecord)
        .filter(BoletaRecord.status == "needs_review")
        .filter(BoletaRecord.reconciled_with_record_id.is_(None))
        .order_by(BoletaRecord.id)
        .all()
    )
    return [_to_detail(r) for r in records]
