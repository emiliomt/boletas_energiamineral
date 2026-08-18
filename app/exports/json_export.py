"""Exports processed records as the exact required output JSON schema."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Boleta, BoletaRecord
from app.schemas import BoletaRecordOut


def build_json_export(db: Session, batch_id: int | None = None) -> list[dict]:
    query = db.query(BoletaRecord).join(Boleta, BoletaRecord.boleta_id == Boleta.id)
    if batch_id is not None:
        query = query.filter(Boleta.batch_id == batch_id)
    records = query.order_by(BoletaRecord.id).all()

    out = []
    for r in records:
        payload = BoletaRecordOut(
            boleta_id=r.folio,
            date=r.date,
            origin=r.origin,
            destination=r.destination,
            material=r.material,
            fletero=r.fletero,
            weight=r.weight,
            weight_source=r.weight_source,
            trip_type=r.trip_type,
            tariff_amount=r.tariff_amount,
            inventory_direction=r.inventory_direction,
            inventory_quantity=r.inventory_quantity,
            confidence_score=r.confidence_score,
            status=r.status,
            exceptions=r.exceptions or [],
        )
        out.append(payload.model_dump())
    return out
