from __future__ import annotations

from app.models import Batch, Boleta, BoletaRecord
from app.reporting.summary import build_batch_summary


def _make_record(db_session, batch, *, status, fletero, tariff_amount, material=None, inventory_quantity=None):
    boleta = Boleta(
        batch_id=batch.id,
        original_filename="x.png",
        stored_path="/tmp/x.png",
        mime_type="image/png",
        page_number=1,
        sha256_hash=f"hash-{fletero}-{tariff_amount}",
    )
    db_session.add(boleta)
    db_session.flush()
    record = BoletaRecord(
        boleta_id=boleta.id,
        status=status,
        fletero=fletero,
        tariff_amount=tariff_amount,
        material=material,
        inventory_quantity=inventory_quantity,
        origin="Mina San Jose",
        destination="Planta Norte",
        exceptions=[],
        field_confidences={},
    )
    db_session.add(record)
    db_session.flush()
    return record


def test_payment_totals_only_count_auto_processed_records(db_session):
    batch = Batch(label="test")
    db_session.add(batch)
    db_session.flush()

    _make_record(db_session, batch, status="auto_processed", fletero="Juan Perez", tariff_amount=850.0)
    _make_record(db_session, batch, status="needs_review", fletero="Juan Perez", tariff_amount=1400.0)
    _make_record(db_session, batch, status="needs_review", fletero="Maria Lopez", tariff_amount=300.0)

    summary = build_batch_summary(db_session, batch.id)

    assert summary.total_boletas == 3
    assert summary.auto_processed_count == 1
    assert summary.needs_review_count == 2
    # Only the auto_processed Juan Perez record should count -- the
    # needs_review ones must not inflate the payable total.
    assert summary.total_payment_by_fletero == {"Juan Perez": 850.0}


def test_inventory_totals_only_count_auto_processed_records(db_session):
    batch = Batch(label="test")
    db_session.add(batch)
    db_session.flush()

    _make_record(
        db_session, batch, status="auto_processed", fletero="Juan Perez", tariff_amount=850.0,
        material="carbon", inventory_quantity=-9000.0,
    )
    _make_record(
        db_session, batch, status="needs_review", fletero="Maria Lopez", tariff_amount=300.0,
        material="carbon", inventory_quantity=-5000.0,
    )

    summary = build_batch_summary(db_session, batch.id)

    assert summary.net_inventory_by_material == {"carbon": -9000.0}
