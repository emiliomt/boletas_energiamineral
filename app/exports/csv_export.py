"""Exports processed records as a flat CSV (same fields as the JSON export,
`exceptions` joined with ';')."""
from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session

from app.exports.json_export import build_json_export

CSV_COLUMNS = [
    "boleta_id",
    "date",
    "origin",
    "destination",
    "material",
    "fletero",
    "weight",
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
    rows = build_json_export(db, batch_id=batch_id)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        flat = dict(row)
        flat["exceptions"] = ";".join(row.get("exceptions") or [])
        writer.writerow(flat)
    return buffer.getvalue()
