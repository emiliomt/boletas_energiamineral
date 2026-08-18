"""Batch-level aggregations: totals, auto vs review split, payment by
fletero, and net inventory movement by material/route."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import Boleta, BoletaRecord
from app.schemas import BatchSummary


def build_batch_summary(db: Session, batch_id: int) -> BatchSummary:
    records = (
        db.query(BoletaRecord)
        .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
        .filter(Boleta.batch_id == batch_id)
        .all()
    )

    total = len(records)
    auto_processed = sum(1 for r in records if r.status == "auto_processed")
    needs_review = total - auto_processed

    payment_by_fletero: dict[str, float] = defaultdict(float)
    inventory_by_material: dict[str, float] = defaultdict(float)
    inventory_by_route: dict[str, float] = defaultdict(float)

    for r in records:
        if r.fletero and r.tariff_amount is not None:
            payment_by_fletero[r.fletero] += r.tariff_amount
        if r.material and r.inventory_quantity is not None:
            inventory_by_material[r.material] += r.inventory_quantity
        if r.origin and r.destination and r.inventory_quantity is not None:
            inventory_by_route[f"{r.origin} -> {r.destination}"] += r.inventory_quantity

    return BatchSummary(
        batch_id=batch_id,
        total_boletas=total,
        auto_processed_count=auto_processed,
        needs_review_count=needs_review,
        total_payment_by_fletero=dict(payment_by_fletero),
        net_inventory_by_material=dict(inventory_by_material),
        net_inventory_by_route=dict(inventory_by_route),
    )
