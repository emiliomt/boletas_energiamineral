"""Exports processed records as a flat CSV (same fields as the JSON export,
`exceptions` joined with ';')."""
from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session

from app.exports.json_export import build_json_export
from app.models import Batch, Boleta, BoletaRecord, Transportista

CSV_COLUMNS = [
    "boleta_id",
    "date",
    "origin",
    "destination",
    "material",
    "fletero",
    "weight",
    "weight_declared",
    "weight_source",
    "trip_type",
    "tariff_amount",
    "inventory_direction",
    "inventory_quantity",
    "confidence_score",
    "status",
    "exceptions",
]


def build_csv_export(db: Session, batch_id: int | None = None) -> str:
    # Entrada ingest-only batches: export reduced provider-slip columns
    if batch_id is not None:
        batch = db.get(Batch, batch_id)
        if batch and batch.kind == "entrada":
            records = (
                db.query(BoletaRecord)
                .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
                .filter(Boleta.batch_id == batch_id)
                .filter(BoletaRecord.reconciled_with_record_id.is_(None))
                .order_by(BoletaRecord.id)
                .all()
            )
            has_measured_weight = any((r.weight_source == "measured" and r.weight is not None) for r in records)
            headers = ["transportista", "fecha", "numero_caja", "chofer"] + (["peso_neto"] if has_measured_weight else [])

            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=headers)
            writer.writeheader()
            transportista_name = ""
            if batch.transportista_id:
                t = db.get(Transportista, batch.transportista_id)
                transportista_name = t.canonical_name if t else ""
            for r in records:
                row = {
                    "transportista": transportista_name or "",
                    "fecha": r.date or "",
                    "numero_caja": r.truck_box_number or "",
                    "chofer": r.fletero or "",
                }
                if has_measured_weight:
                    row["peso_neto"] = (
                        f"{r.weight:.0f}" if (r.weight_source == "measured" and r.weight is not None) else ""
                    )
                writer.writerow(row)
            return buffer.getvalue()

    rows = build_json_export(db, batch_id=batch_id)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        flat = dict(row)
        flat["exceptions"] = ";".join(row.get("exceptions") or [])
        writer.writerow(flat)
    return buffer.getvalue()
