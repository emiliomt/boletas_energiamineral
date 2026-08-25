from __future__ import annotations

from app.engines.salida_reconciliation import compute_delivered_weight, find_salida_counterpart
from app.models import Batch, Boleta, BoletaRecord


def _seed_boleta(db_session, batch: Batch, document_type: str, filename: str) -> Boleta:
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=filename,
        stored_path=f"/tmp/{filename}",
        mime_type="image/png",
        page_number=1,
        sha256_hash=f"hash-{filename}",
        document_type=document_type,
    )
    db_session.add(boleta)
    db_session.flush()
    return boleta


def _seed_partial_record(
    db_session, batch: Batch, document_type: str, folio: str | None, filename: str
) -> BoletaRecord:
    boleta = _seed_boleta(db_session, batch, document_type, filename)
    record = BoletaRecord(
        boleta_id=boleta.id,
        kind="salida",
        folio=folio,
        salida_status="boleta_only" if document_type == "boleta" else "cfe_slip_only",
    )
    db_session.add(record)
    db_session.flush()
    return record


def test_boleta_first_no_counterpart_yet_stays_boleta_only(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()

    match = find_salida_counterpart(db_session, batch.id, "S-1001", "boleta")

    assert match.salida_status == "boleta_only"
    assert match.counterpart_record is None
    assert match.exceptions == []


def test_slip_first_no_counterpart_yet_stays_cfe_slip_only(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()

    match = find_salida_counterpart(db_session, batch.id, "S-1002", "cfe_slip")

    assert match.salida_status == "cfe_slip_only"
    assert match.counterpart_record is None


def test_boleta_then_slip_completes(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta_record = _seed_partial_record(db_session, batch, "boleta", "S-1003", "b.png")

    match = find_salida_counterpart(db_session, batch.id, "S-1003", "cfe_slip")

    assert match.salida_status == "complete"
    assert match.counterpart_record is not None
    assert match.counterpart_record.id == boleta_record.id


def test_slip_then_boleta_completes(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    slip_record = _seed_partial_record(db_session, batch, "cfe_slip", "S-1004", "s.png")

    match = find_salida_counterpart(db_session, batch.id, "S-1004", "boleta")

    assert match.salida_status == "complete"
    assert match.counterpart_record is not None
    assert match.counterpart_record.id == slip_record.id


def test_both_together_matching_folios_completes(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta_record = _seed_partial_record(db_session, batch, "boleta", "S-1005", "b.png")

    match = find_salida_counterpart(db_session, batch.id, "S-1005", "cfe_slip")

    assert match.salida_status == "complete"
    assert match.counterpart_record.id == boleta_record.id
    assert match.exceptions == []


def test_both_together_mismatched_folios_flags_both(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta_record = _seed_partial_record(db_session, batch, "boleta", "S-2001", "b.png")

    match = find_salida_counterpart(db_session, batch.id, "S-2002", "cfe_slip")

    assert match.salida_status == "cfe_slip_only"
    assert match.counterpart_record is None
    assert "salida_folio_mismatch" in match.exceptions
    assert match.mismatched_sibling is not None
    assert match.mismatched_sibling.id == boleta_record.id


def test_different_batches_do_not_mismatch(db_session):
    # Two documents with different folios in *different* batches were never
    # uploaded as a pair -- no mismatch, just two independent partials.
    batch_a = Batch(label="a", kind="salida")
    batch_b = Batch(label="b", kind="salida")
    db_session.add_all([batch_a, batch_b])
    db_session.flush()
    _seed_partial_record(db_session, batch_a, "boleta", "S-3001", "b.png")

    match = find_salida_counterpart(db_session, batch_b.id, "S-3002", "cfe_slip")

    assert match.salida_status == "cfe_slip_only"
    assert match.exceptions == []
    assert match.mismatched_sibling is None


def test_reprocessing_an_already_complete_pairing_is_excluded_from_rematch(db_session):
    """Idempotency: once a folio's counterpart is already reconciled
    (reconciled_with_record_id set), it must not be matched again."""
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    primary = _seed_partial_record(db_session, batch, "boleta", "S-4001", "b.png")
    primary.salida_status = "complete"
    secondary = _seed_partial_record(db_session, batch, "cfe_slip", "S-4001", "s.png")
    secondary.salida_status = "cfe_slip_only"
    secondary.reconciled_with_record_id = primary.id
    db_session.flush()

    # A third document (e.g. a stray reprocess) looking for a "cfe_slip_only"
    # counterpart must not find the already-reconciled secondary.
    match = find_salida_counterpart(db_session, batch.id, "S-4001", "boleta", exclude_record_id=primary.id)

    assert match.counterpart_record is None


def test_exclude_record_id_excludes_self_from_matching(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    record = _seed_partial_record(db_session, batch, "boleta", "S-5001", "b.png")

    match = find_salida_counterpart(db_session, batch.id, "S-5001", "cfe_slip", exclude_record_id=record.id + 999)
    assert match.counterpart_record is not None  # sanity: normally matches

    match_excluded = find_salida_counterpart(db_session, batch.id, "S-5001", "boleta", exclude_record_id=record.id)
    assert match_excluded.counterpart_record is None


# --- compute_delivered_weight ----------------------------------------------


def test_delivered_weight_exit_greater_than_entry():
    assert compute_delivered_weight(entry_weight=500.0, exit_weight=9500.0) == 9000.0


def test_delivered_weight_entry_greater_than_exit_still_positive():
    # Never negative, regardless of which raw OCR'd value is larger.
    assert compute_delivered_weight(entry_weight=9500.0, exit_weight=500.0) == 9000.0


def test_delivered_weight_missing_entry_is_none():
    assert compute_delivered_weight(entry_weight=None, exit_weight=9500.0) is None


def test_delivered_weight_missing_exit_is_none():
    assert compute_delivered_weight(entry_weight=500.0, exit_weight=None) is None


def test_delivered_weight_both_missing_is_none():
    assert compute_delivered_weight(entry_weight=None, exit_weight=None) is None


def test_delivered_weight_equal_entry_and_exit_is_zero():
    assert compute_delivered_weight(entry_weight=5000.0, exit_weight=5000.0) == 0.0
