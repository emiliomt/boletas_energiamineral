from __future__ import annotations

from app.engines.folio_registry import check_folio, link_folio, unlink_folio
from app.models import Folio, FolioBatch


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
