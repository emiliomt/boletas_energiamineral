"""Exports a FolioBatch's folios as CSV -- an alternate deliverable for a
print vendor with their own QR rendering pipeline, or for the admin's own
records. Same shape as csv_export.py/json_export.py."""
from __future__ import annotations

import csv
import io

from app.models import Folio

CSV_COLUMNS = ["folio", "qr_payload", "status"]


def build_folio_batch_csv(folios: list[Folio]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for f in folios:
        writer.writerow({"folio": f.folio, "qr_payload": f.qr_payload, "status": f.status})
    return buffer.getvalue()
