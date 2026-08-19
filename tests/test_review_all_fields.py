"""A reviewer can correct every boleta field (not just the original handful),
including the coal-quality metrics stored in quality_data."""
from __future__ import annotations

from app.models import Batch, Boleta, BoletaRecord
from app.review.service import apply_review
from app.schemas import ReviewCorrection


def _make_record(db_session) -> BoletaRecord:
    batch = Batch(label="rev")
    db_session.add(batch)
    db_session.flush()
    boleta = Boleta(
        batch_id=batch.id,
        original_filename="b.png",
        stored_path="b.png",
        mime_type="image/png",
        page_number=1,
        sha256_hash="n/a",
    )
    db_session.add(boleta)
    db_session.flush()
    record = BoletaRecord(boleta_id=boleta.id, status="needs_review")
    db_session.add(record)
    db_session.flush()
    return record


def test_reviewer_can_correct_all_boleta_fields(db_session):
    record = _make_record(db_session)

    correction = ReviewCorrection(
        action="correct",
        edited_by="tester",
        folio="B-6001",
        date="2026-08-19",
        proveedor="ENERGIA MINERAL, S.A. DE C.V.",
        origin="Tajo San Jose",
        secondary_origin="Patio Rosita",
        destination="C.T. Jose Lopez Portillo",
        contract_number="700544405",
        concesion_minera="Mota del cura y el Carrizo No.1T-198196",
        fletero="Luis Perez",
        truck_box_number="329",
        weight_declared=1000.0,
        weight=900.0,
        representante_legal="Andres Montemayor",
        poder_calorifico_superior="6200",
        humedad_pct="8.5",
        granulometria="50mm",
    )

    apply_review(db_session, record, correction)

    assert record.proveedor == "ENERGIA MINERAL, S.A. DE C.V."
    assert record.secondary_origin == "Patio Rosita"
    assert record.contract_number == "700544405"
    assert record.concesion_minera == "Mota del cura y el Carrizo No.1T-198196"
    assert record.truck_box_number == "329"
    assert record.weight_declared == 1000.0
    assert record.representante_legal == "Andres Montemayor"
    assert record.quality_data["poder_calorifico_superior"] == "6200"
    assert record.quality_data["humedad_pct"] == "8.5"
    assert record.quality_data["granulometria"] == "50mm"
    # and the original handful still work
    assert record.folio == "B-6001"
    assert record.destination == "C.T. Jose Lopez Portillo"
    assert record.weight == 900.0
