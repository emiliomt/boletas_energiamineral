"""Phase 3 regression coverage for app/review/service.py::apply_review's
kind/salida_status awareness: a still-partial Salida record can't be
force-completed via manual approval, and tariff computation routes through
the correct engine (PricingRule for Entrada/complete-Salida, not the old
TariffRule path) when a reviewer corrects fields."""
from __future__ import annotations

from app.models import Batch, Boleta, BoletaRecord, Producer
from app.review.service import apply_review
from app.schemas import ReviewCorrection


def _make_boleta(db_session, batch: Batch, filename: str = "b.png") -> Boleta:
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=filename,
        stored_path=filename,
        mime_type="image/png",
        page_number=1,
        sha256_hash=f"hash-{filename}",
    )
    db_session.add(boleta)
    db_session.flush()
    return boleta


def test_approve_on_partial_boleta_only_record_does_not_force_complete(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta = _make_boleta(db_session, batch)
    record = BoletaRecord(
        boleta_id=boleta.id, kind="salida", salida_status="boleta_only", status="needs_review",
        origin="Mina San Jose", destination="Planta Norte",
    )
    db_session.add(record)
    db_session.flush()

    correction = ReviewCorrection(action="approve", edited_by="tester", fletero="Juan Perez")
    apply_review(db_session, record, correction)

    assert record.status == "needs_review"
    assert record.salida_status == "boleta_only"
    assert record.tariff_amount is None
    assert record.inventory_direction == "unknown"


def test_approve_on_partial_cfe_slip_only_record_does_not_force_complete(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta = _make_boleta(db_session, batch)
    record = BoletaRecord(
        boleta_id=boleta.id, kind="salida", salida_status="cfe_slip_only", status="needs_review", folio="S-1",
    )
    db_session.add(record)
    db_session.flush()

    apply_review(db_session, record, ReviewCorrection(action="approve", edited_by="tester"))

    assert record.status == "needs_review"
    assert record.tariff_amount is None


def test_approve_on_complete_salida_record_still_works(db_session):
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta = _make_boleta(db_session, batch)
    record = BoletaRecord(
        boleta_id=boleta.id, kind="salida", salida_status="complete", status="needs_review",
        origin="Mina San Jose", destination="Planta Norte", delivered_weight=9000.0, weight=9000.0,
    )
    db_session.add(record)
    db_session.flush()

    apply_review(db_session, record, ReviewCorrection(action="approve", edited_by="tester"))

    assert record.status == "auto_processed"
    # Real placeholder PricingRule P004 (Mina San Jose, per_weight, 0.10/kg).
    assert record.tariff_amount == 900.0
    assert record.matched_tariff_rule_id is None  # PricingRule, not TariffRule -- FK-safety


def test_approve_on_legacy_salida_record_with_no_salida_status_still_works(db_session):
    # salida_status is None -- a pre-Phase-3 record that never went through
    # reconciliation -- treated as already-complete for backward compatibility.
    batch = Batch(label="b1", kind="salida")
    db_session.add(batch)
    db_session.flush()
    boleta = _make_boleta(db_session, batch)
    record = BoletaRecord(
        boleta_id=boleta.id, kind="salida", salida_status=None, status="needs_review",
        origin="Mina San Jose", destination="Planta Norte", weight=9000.0,
    )
    db_session.add(record)
    db_session.flush()

    apply_review(db_session, record, ReviewCorrection(action="approve", edited_by="tester"))

    assert record.status == "auto_processed"


def test_correcting_entrada_record_uses_producer_scoped_pricing(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()  # per_weight, 120.00/ton
    batch = Batch(label="e1", kind="entrada", producer_id=producer.id)
    db_session.add(batch)
    db_session.flush()
    boleta = _make_boleta(db_session, batch)
    record = BoletaRecord(
        boleta_id=boleta.id, kind="entrada", producer_id=producer.id, status="needs_review", weight=10.0,
    )
    db_session.add(record)
    db_session.flush()

    apply_review(db_session, record, ReviewCorrection(action="correct", edited_by="tester", fletero="Juan Perez"))

    # Not the old TariffRule engine -- compute_entrada_tariff via PricingRule.
    assert record.tariff_amount == 1200.0
    assert record.matched_tariff_rule_id is None
