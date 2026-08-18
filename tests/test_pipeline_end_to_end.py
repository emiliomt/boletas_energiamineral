from __future__ import annotations

from app.models import Batch, Boleta, Folio, FolioBatch
from app.pipeline.orchestrator import process_boleta
from tests.fakes import FakeOCRAdapter

CLEAN_TEXT = """\
Folio: B-1001
Fecha: 15/01/2026
Centro de Explotacion: Mina San Jose
Destino: Planta Norte
Datos del chofer del camion: Juan Perez
Volumen por Entregar: 9000
Volumen Entregado: 9000 kg
"""

MISSING_WEIGHT_TEXT = """\
Folio: B-1002
Fecha: 16/01/2026
Centro de Explotacion: Planta Norte
Destino: Patio Almacen
Datos del chofer del camion: Maria Lopez
"""

ILLEGIBLE_TEXT = "xk qlm zzt ### asdf ??"


def _seed_folio(db_session, folio: str) -> Folio:
    """These fixtures pre-date the folio registry, so their folios (parsed
    from plain OCR text, no QR involved) need to be pre-issued or every
    test would get flagged `unknown_folio`."""
    batch = FolioBatch(label=f"seed-{folio}", mode="imported", count=1)
    db_session.add(batch)
    db_session.flush()
    row = Folio(folio_batch_id=batch.id, folio=folio, qr_payload=f"BOL:{folio}")
    db_session.add(row)
    db_session.flush()
    return row


def _make_boleta(db_session, batch_label: str, filename: str) -> Boleta:
    batch = Batch(label=batch_label)
    db_session.add(batch)
    db_session.flush()
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=filename,
        stored_path=f"/tmp/{filename}",  # never opened by FakeOCRAdapter
        mime_type="image/png",
        page_number=1,
        sha256_hash="deadbeef",
    )
    db_session.add(boleta)
    db_session.flush()
    return boleta


def test_clean_boleta_is_auto_processed_with_measured_weight(db_session):
    _seed_folio(db_session, "B-1001")
    boleta = _make_boleta(db_session, "batch-1", "clean.png")
    adapter = FakeOCRAdapter(text=CLEAN_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.status == "auto_processed"
    assert record.exceptions == []
    assert record.trip_type == "acarreo_carbon"
    assert record.weight == 9000.0
    assert record.weight_source == "measured"
    assert record.tariff_amount == 850.0
    assert record.inventory_direction == "outbound"
    assert record.inventory_quantity == -9000.0


def test_missing_weight_boleta_uses_estimation_rule(db_session):
    _seed_folio(db_session, "B-1002")
    boleta = _make_boleta(db_session, "batch-1", "missing_weight.png")
    adapter = FakeOCRAdapter(text=MISSING_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.weight_source == "estimated"
    assert record.weight == 8000.0
    assert record.trip_type == "transferencia_interna"
    assert record.inventory_direction == "none"
    assert "missing_weight_no_estimate" not in record.exceptions


def test_illegible_boleta_goes_to_needs_review(db_session):
    boleta = _make_boleta(db_session, "batch-1", "illegible.png")
    adapter = FakeOCRAdapter(text=ILLEGIBLE_TEXT, confidence=12.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.status == "needs_review"
    assert "low_ocr_confidence" in record.exceptions
    assert any(e.startswith("missing_required_field:") for e in record.exceptions)
    assert record.confidence_score < 0.75


def test_volumen_mismatch_flags_needs_review(db_session):
    _seed_folio(db_session, "B-1003")
    boleta = _make_boleta(db_session, "batch-1", "mismatch.png")
    text = (
        "Folio: B-1003\n"
        "Fecha: 15/01/2026\n"
        "Centro de Explotacion: Mina San Jose\n"
        "Destino: Planta Norte\n"
        "Datos del chofer del camion: Juan Perez\n"
        "Volumen por Entregar: 9000\n"
        "Volumen Entregado: 7000 kg\n"
    )
    adapter = FakeOCRAdapter(text=text, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.status == "needs_review"
    assert "volumen_mismatch" in record.exceptions
    assert record.weight == 7000.0
    assert record.weight_declared == 9000.0


def test_reprocessing_updates_existing_record_instead_of_duplicating(db_session):
    _seed_folio(db_session, "B-1001")
    boleta = _make_boleta(db_session, "batch-1", "clean2.png")
    adapter = FakeOCRAdapter(text=CLEAN_TEXT, confidence=95.0)

    first = process_boleta(db_session, boleta, adapter)
    second = process_boleta(db_session, boleta, adapter)

    assert first.id == second.id
