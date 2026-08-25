from __future__ import annotations

from app.engines.folio_registry import check_entrada_folio, check_folio, link_folio, unlink_folio
from app.models import Batch, Boleta, BoletaRecord, Folio, FolioBatch, Producer


def _seed(db_session, folio: str, status: str = "issued", boleta_record_id: int | None = None) -> Folio:
    batch = FolioBatch(label="test-batch", mode="imported", count=1)
    db_session.add(batch)
    db_session.flush()
    row = Folio(
        folio_batch_id=batch.id,
        folio=folio,
        qr_payload=f"BOL:{folio}",
        status=status,
        boleta_record_id=boleta_record_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_no_folio_returns_no_qr_without_exception(db_session):
    result = check_folio(db_session, None)

    assert result.status == "no_qr"
    assert result.exceptions == []


def test_unseeded_folio_is_unknown(db_session):
    result = check_folio(db_session, "B-9999")

    assert result.status == "unknown"
    assert "unknown_folio" in result.exceptions


def test_seeded_issued_folio_is_ok(db_session):
    _seed(db_session, "B-1000", status="issued")

    result = check_folio(db_session, "B-1000")

    assert result.status == "ok"
    assert result.folio_row is not None
    assert result.exceptions == []


def test_seeded_scanned_folio_linked_to_other_record_is_already_used(db_session):
    _seed(db_session, "B-1001", status="scanned", boleta_record_id=42)

    result = check_folio(db_session, "B-1001", exclude_record_id=99)

    assert result.status == "already_used"
    assert "folio_already_used" in result.exceptions


def test_seeded_scanned_folio_linked_to_same_record_is_ok(db_session):
    _seed(db_session, "B-1002", status="scanned", boleta_record_id=42)

    result = check_folio(db_session, "B-1002", exclude_record_id=42)

    assert result.status == "ok"
    assert result.exceptions == []


def test_link_folio_marks_scanned_and_links_record(db_session):
    row = _seed(db_session, "B-2000", status="issued")

    link_folio(row, record_id=7)

    assert row.status == "scanned"
    assert row.boleta_record_id == 7
    assert row.scanned_at is not None


def test_unlink_folio_reverts_to_issued(db_session):
    row = _seed(db_session, "B-2001", status="scanned", boleta_record_id=7)

    unlink_folio(row)

    assert row.status == "issued"
    assert row.boleta_record_id is None
    assert row.scanned_at is None


# --- check_entrada_folio (Phase 2) -----------------------------------------


def _seed_entrada_record(db_session, producer_id: int, folio: str) -> BoletaRecord:
    """A minimal ingested Entrada BoletaRecord -- check_entrada_folio dedups
    against previously ingested records, not a pre-issued Folio registry."""
    batch = Batch(label="test-entrada-batch", kind="entrada", producer_id=producer_id)
    db_session.add(batch)
    db_session.flush()
    boleta = Boleta(
        batch_id=batch.id,
        original_filename="test.png",
        stored_path="/tmp/test.png",
        mime_type="image/png",
        sha256_hash=f"hash-{folio}",
    )
    db_session.add(boleta)
    db_session.flush()
    record = BoletaRecord(boleta_id=boleta.id, kind="entrada", producer_id=producer_id, folio=folio)
    db_session.add(record)
    db_session.flush()
    return record


def _seed_producer(db_session, name: str) -> Producer:
    producer = Producer(name=name, default_origin=name, active=True)
    db_session.add(producer)
    db_session.flush()
    return producer


def test_entrada_no_folio_returns_no_qr_without_exception(db_session):
    result = check_entrada_folio(db_session, producer_id=1, folio=None)

    assert result.status == "no_qr"
    assert result.exceptions == []


def test_entrada_same_folio_different_producer_no_flag(db_session):
    producer_a = _seed_producer(db_session, "TEST Producer A")
    producer_b = _seed_producer(db_session, "TEST Producer B")
    _seed_entrada_record(db_session, producer_a.id, "E-1000")

    result = check_entrada_folio(db_session, producer_id=producer_b.id, folio="E-1000")

    assert result.status == "ok"
    assert result.exceptions == []


def test_entrada_same_folio_same_producer_flags_exception(db_session):
    producer = _seed_producer(db_session, "TEST Producer C")
    _seed_entrada_record(db_session, producer.id, "E-1001")

    result = check_entrada_folio(db_session, producer_id=producer.id, folio="E-1001")

    assert result.status == "already_used"
    assert "folio_already_used_for_producer" in result.exceptions


def test_entrada_reprocess_of_same_record_no_false_flag(db_session):
    producer = _seed_producer(db_session, "TEST Producer D")
    record = _seed_entrada_record(db_session, producer.id, "E-1002")

    result = check_entrada_folio(db_session, producer_id=producer.id, folio="E-1002", exclude_record_id=record.id)

    assert result.status == "ok"
    assert result.exceptions == []


def test_entrada_folio_check_never_sets_folio_row(db_session):
    # No Folio-table lookup at all for Entradas -- orchestrator's
    # `if folio_check.folio_row is not None: link_folio(...)` guard must
    # never fire for this check.
    producer = _seed_producer(db_session, "TEST Producer E")
    _seed_entrada_record(db_session, producer.id, "E-1003")

    result = check_entrada_folio(db_session, producer_id=producer.id, folio="E-1003")

    assert result.folio_row is None
