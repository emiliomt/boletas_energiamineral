"""Salida two-document reconciliation (Phase 3): a Salida boleta and its
CFE weight slip arrive as two separate scans sharing a folio. Neither can
proceed to pricing/inventory alone (app/pipeline/orchestrator.py gates
that on `salida_status == "complete"`) -- this module answers "what state
is this pairing in" by looking for an existing partial counterpart. The
actual merging of both documents' data onto one record, and computing
tariff/inventory once complete, happens in the orchestrator, which has
direct access to both records' full field data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Boleta, BoletaRecord


def _pending_status_for(document_type: str) -> str:
    return "boleta_only" if document_type == "boleta" else "cfe_slip_only"


def _opposite_pending_status(document_type: str) -> str:
    return "cfe_slip_only" if document_type == "boleta" else "boleta_only"


@dataclass
class ReconciliationMatch:
    salida_status: str  # boleta_only | cfe_slip_only | complete
    counterpart_record: BoletaRecord | None = None  # set only when salida_status == "complete"
    mismatched_sibling: BoletaRecord | None = None  # set only on a same-batch folio mismatch
    exceptions: list[str] = field(default_factory=list)


def find_salida_counterpart(
    db: Session,
    batch_id: int,
    folio: str | None,
    document_type: str,
    exclude_record_id: int | None = None,
) -> ReconciliationMatch:
    """Looks for an existing partial BoletaRecord this document completes.

    - Same folio, opposite document type, still pending (and not already
      reconciled into something else) -> "complete".
    - No folio match, but a same-batch sibling of the opposite type is
      still pending with a *different* folio -> the pair was clearly
      uploaded together but the folios disagree; flag
      `salida_folio_mismatch` rather than silently leaving two
      unrelated-looking partials sitting in the queue. The caller is
      expected to also retroactively flag `mismatched_sibling` itself
      (this function only reports it, it doesn't mutate anything).
    - Otherwise -> this document starts (or stays) its own pending state.
    """
    own_status = _pending_status_for(document_type)
    opposite_status = _opposite_pending_status(document_type)

    if folio:
        query = db.query(BoletaRecord).filter(
            BoletaRecord.kind == "salida",
            BoletaRecord.folio == folio,
            BoletaRecord.salida_status == opposite_status,
            BoletaRecord.reconciled_with_record_id.is_(None),
        )
        if exclude_record_id is not None:
            query = query.filter(BoletaRecord.id != exclude_record_id)
        counterpart = query.order_by(BoletaRecord.id).first()
        if counterpart is not None:
            return ReconciliationMatch(salida_status="complete", counterpart_record=counterpart)

    sibling_query = (
        db.query(BoletaRecord)
        .join(Boleta, BoletaRecord.boleta_id == Boleta.id)
        .filter(
            BoletaRecord.kind == "salida",
            Boleta.batch_id == batch_id,
            BoletaRecord.salida_status == opposite_status,
            BoletaRecord.reconciled_with_record_id.is_(None),
        )
    )
    if exclude_record_id is not None:
        sibling_query = sibling_query.filter(BoletaRecord.id != exclude_record_id)
    sibling = sibling_query.order_by(BoletaRecord.id).first()
    if sibling is not None and sibling.folio != folio:
        return ReconciliationMatch(
            salida_status=own_status,
            mismatched_sibling=sibling,
            exceptions=["salida_folio_mismatch"],
        )

    return ReconciliationMatch(salida_status=own_status)


def compute_delivered_weight(entry_weight: float | None, exit_weight: float | None) -> float | None:
    """Delivered weight is always |exit - entry|, never negative, regardless
    of which raw OCR'd value happens to be larger."""
    if entry_weight is None or exit_weight is None:
        return None
    return abs(exit_weight - entry_weight)
