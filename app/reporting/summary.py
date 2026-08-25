"""Batch-level aggregations: totals, auto vs review split, payment by
fletero, and net inventory movement by material/route."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Batch, Boleta, BoletaRecord
from app.schemas import BatchSummary


@dataclass
class OverviewRow:
    """One boleta row for the admin dashboard (record + its lote label)."""

    record: BoletaRecord
    batch_label: str


@dataclass
class Overview:
    """Everything the admin dashboard shows: the filtered boleta rows plus the
    same aggregates the Excel export summarizes, computed across the selection
    (not just one batch)."""

    rows: list[OverviewRow] = field(default_factory=list)
    total_boletas: int = 0
    auto_processed_count: int = 0
    needs_review_count: int = 0
    total_payable: float = 0.0
    total_payment_by_fletero: dict[str, float] = field(default_factory=dict)
    net_inventory_by_material: dict[str, float] = field(default_factory=dict)
    net_inventory_by_route: dict[str, float] = field(default_factory=dict)


def build_overview(
    db: Session,
    batch_id: int | None = None,
    status: str | None = None,
    fletero: str | None = None,
) -> Overview:
    """Aggregates boletas across an optional (batch / status / fletero) filter
    for the admin dashboard. Money/inventory totals count only auto_processed
    records, matching build_batch_summary and the Excel 'Pagar' gate."""
    query = (
        db.query(BoletaRecord, Batch.label)
        .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
        .join(Batch, Boleta.batch_id == Batch.id)
    )
    if batch_id is not None:
        query = query.filter(Boleta.batch_id == batch_id)
    if status:
        query = query.filter(BoletaRecord.status == status)
    if fletero:
        query = query.filter(BoletaRecord.fletero == fletero)

    results = query.order_by(BoletaRecord.id.desc()).all()

    payment_by_fletero: dict[str, float] = defaultdict(float)
    inventory_by_material: dict[str, float] = defaultdict(float)
    inventory_by_route: dict[str, float] = defaultdict(float)
    auto = 0
    total_payable = 0.0

    for record, _label in results:
        if record.status != "auto_processed":
            continue
        auto += 1
        if record.fletero and record.tariff_amount is not None:
            payment_by_fletero[record.fletero] += record.tariff_amount
            total_payable += record.tariff_amount
        if record.material and record.inventory_quantity is not None:
            inventory_by_material[record.material] += record.inventory_quantity
        if record.origin and record.destination and record.inventory_quantity is not None:
            inventory_by_route[f"{record.origin} -> {record.destination}"] += record.inventory_quantity

    total = len(results)
    return Overview(
        rows=[OverviewRow(record=r, batch_label=label) for r, label in results],
        total_boletas=total,
        auto_processed_count=auto,
        needs_review_count=total - auto,
        total_payable=total_payable,
        total_payment_by_fletero=dict(payment_by_fletero),
        net_inventory_by_material=dict(inventory_by_material),
        net_inventory_by_route=dict(inventory_by_route),
    )


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

    # Only records the pipeline actually confirmed (auto_processed) count
    # toward money/inventory totals -- a needs_review record hasn't been
    # verified yet (wrong folio, volumen mismatch, etc.) and must not
    # inflate what's shown as owed or moved until a reviewer approves it.
    for r in records:
        if r.status != "auto_processed":
            continue
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
