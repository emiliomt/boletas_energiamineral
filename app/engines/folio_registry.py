"""Validates a scanned folio against the folios we actually issued (see
app/models.py's FolioBatch/Folio). Same shape as the other lookup engines
(classification.py/tariff.py/inventory.py): a dataclass result carrying its
own `.exceptions` list that `exceptions.evaluate()` folds into the combined
list — not part of evaluate()'s own scoring/aggregation job.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Folio


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class FolioCheckResult:
    status: str  # ok | unknown | already_used | no_qr
    folio_row: Folio | None = None
    exceptions: list[str] = field(default_factory=list)


def check_folio(db: Session, folio: str | None, exclude_record_id: int | None = None) -> FolioCheckResult:
    """Looks up `folio` in the issued-folio registry.

    - No folio at all (QR didn't decode and OCR found nothing) -> "no_qr",
      no exception here; missing-folio is already covered generically by
      exceptions.evaluate()'s `missing_required_field:folio` check.
    - Folio not found in any batch -> "unknown", hard-block exception.
    - Folio found but already linked to a *different* scanned record ->
      "already_used", hard-block exception.
    - Folio found and either unlinked or linked to this same record
      (reprocess/re-review case, via `exclude_record_id`) -> "ok".
    """
    if not folio:
        return FolioCheckResult(status="no_qr")

    row = db.query(Folio).filter_by(folio=folio).one_or_none()
    if row is None:
        return FolioCheckResult(status="unknown", exceptions=["unknown_folio"])

    if row.status == "scanned" and row.boleta_record_id != exclude_record_id:
        return FolioCheckResult(status="already_used", folio_row=row, exceptions=["folio_already_used"])

    return FolioCheckResult(status="ok", folio_row=row)


def link_folio(folio_row: Folio, record_id: int) -> None:
    """Marks an issued Folio as scanned and linked to the given BoletaRecord."""
    folio_row.status = "scanned"
    folio_row.scanned_at = _utcnow()
    folio_row.boleta_record_id = record_id


def unlink_folio(folio_row: Folio) -> None:
    """Reverts a Folio back to issued (e.g. a reviewer corrected the folio
    away from this one)."""
    folio_row.status = "issued"
    folio_row.scanned_at = None
    folio_row.boleta_record_id = None
