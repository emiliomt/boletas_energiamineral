"""End-to-end pipeline tests via FakeOCRAdapter, deterministic (no real
OCR). Since Phase 3, a Salida record only prices/posts inventory once its
matching CFE slip has been reconciled -- a single boleta scan alone lands
in `salida_status="boleta_only"`, needs_review, with no tariff computed.
See tests/test_pipeline_entrada.py for the Entrada (single-document) flow,
and tests/test_salida_reconciliation.py for reconciliation-matcher unit
tests independent of the full pipeline."""
from __future__ import annotations

from app.models import Batch, Boleta, BoletaRecord, Folio, FolioBatch
from app.pipeline.orchestrator import process_boleta
from tests.fakes import FakeOCRAdapter

CLEAN_BOLETA_TEXT = """\
Folio: B-1001
Fecha: 15/01/2026
Centro de Explotacion: Mina San Jose
Destino: Planta Norte
Datos del chofer del camion: Juan Perez
"""

CLEAN_SLIP_TEXT = """\
Folio: B-1001
Fecha: 15/01/2026
Peso de Entrada: 500 kg
Peso de Salida: 9500 kg
"""

TRANSFER_BOLETA_TEXT = """\
Folio: B-1002
Fecha: 16/01/2026
Centro de Explotacion: Planta Norte
Destino: Patio Almacen
Datos del chofer del camion: Maria Lopez
"""

# No weight fields at all -- exercises the WeightEstimationRule fallback
# once the pairing completes.
SLIP_WITHOUT_WEIGHT_TEXT = "Folio: B-1002\nFecha: 16/01/2026\n"

ILLEGIBLE_TEXT = "xk qlm zzt ### asdf ??"


def _seed_folio(db_session, folio: str) -> Folio:
    """These fixtures pre-date the folio registry, so their folios (parsed
    from plain OCR text, no QR involved) need to be pre-issued or every
    test would get flagged `unknown_folio`. Only the "boleta" document type
    is checked against this registry -- see check_folio's Phase 3 scoping."""
    batch = FolioBatch(label=f"seed-{folio}", mode="imported", count=1)
    db_session.add(batch)
    db_session.flush()
    row = Folio(folio_batch_id=batch.id, folio=folio, qr_payload=f"BOL:{folio}")
    db_session.add(row)
    db_session.flush()
    return row


def _make_batch(db_session, label: str, kind: str = "salida") -> Batch:
    batch = Batch(label=label, kind=kind)
    db_session.add(batch)
    db_session.flush()
    return batch


def _make_boleta(db_session, batch: Batch, filename: str, document_type: str = "boleta") -> Boleta:
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=filename,
        stored_path=f"/tmp/{filename}",  # never opened by FakeOCRAdapter
        mime_type="image/png",
        page_number=1,
        sha256_hash=f"hash-{filename}",
        document_type=document_type,
    )
    db_session.add(boleta)
    db_session.flush()
    return boleta


def test_boleta_only_stays_partial_pending_cfe_slip(db_session):
    _seed_folio(db_session, "B-1001")
    batch = _make_batch(db_session, "batch-1")
    boleta = _make_boleta(db_session, batch, "clean_boleta.png")

    record = process_boleta(db_session, boleta, FakeOCRAdapter(text=CLEAN_BOLETA_TEXT, confidence=95.0))

    assert record.kind == "salida"
    assert record.salida_status == "boleta_only"
    assert record.status == "needs_review"  # a partial record is never "done"
    assert record.trip_type == "acarreo_carbon"  # classification doesn't need the CFE side
    assert record.tariff_amount is None
    assert record.inventory_direction == "unknown"
    assert record.inventory_quantity is None


def test_uploading_both_documents_in_one_batch_completes_and_prices(db_session):
    _seed_folio(db_session, "B-1001")
    batch = _make_batch(db_session, "batch-1")
    boleta = _make_boleta(db_session, batch, "clean_boleta.png", document_type="boleta")
    slip = _make_boleta(db_session, batch, "clean_slip.png", document_type="cfe_slip")

    boleta_record = process_boleta(db_session, boleta, FakeOCRAdapter(text=CLEAN_BOLETA_TEXT, confidence=95.0))
    assert boleta_record.salida_status == "boleta_only"

    record = process_boleta(db_session, slip, FakeOCRAdapter(text=CLEAN_SLIP_TEXT, confidence=95.0))

    assert record.salida_status == "complete"
    assert record.status == "auto_processed"
    assert record.exceptions == []
    assert record.trip_type == "acarreo_carbon"
    assert record.cfe_entry_weight == 500.0
    assert record.cfe_exit_weight == 9500.0
    assert record.delivered_weight == 9000.0
    assert record.weight == 9000.0
    assert record.weight_source == "measured"
    assert record.tariff_amount == 900.0  # 0.10 MXN/kg * 9000kg, PricingRule P004
    assert record.inventory_direction == "outbound"
    assert record.inventory_quantity == -9000.0

    # The boleta arrived first, so it's the primary; the slip's own record
    # (created when it was processed) is superseded, not duplicated.
    assert record.id == boleta_record.id
    slip_own_record = db_session.query(BoletaRecord).filter_by(boleta_id=slip.id).one()
    assert slip_own_record.reconciled_with_record_id == record.id


def test_slip_before_boleta_completes_and_prices_the_same_way(db_session):
    """Order-independent: the CFE slip can legitimately arrive first."""
    _seed_folio(db_session, "B-1001")
    batch = _make_batch(db_session, "batch-1")
    slip = _make_boleta(db_session, batch, "slip_first.png", document_type="cfe_slip")
    boleta = _make_boleta(db_session, batch, "boleta_second.png", document_type="boleta")

    slip_record = process_boleta(db_session, slip, FakeOCRAdapter(text=CLEAN_SLIP_TEXT, confidence=95.0))
    assert slip_record.salida_status == "cfe_slip_only"

    record = process_boleta(db_session, boleta, FakeOCRAdapter(text=CLEAN_BOLETA_TEXT, confidence=95.0))

    assert record.salida_status == "complete"
    assert record.tariff_amount == 900.0
    assert record.inventory_direction == "outbound"


def test_complete_pairing_uses_estimation_rule_when_slip_has_no_weight(db_session):
    _seed_folio(db_session, "B-1002")
    batch = _make_batch(db_session, "batch-1")
    boleta = _make_boleta(db_session, batch, "transfer_boleta.png", document_type="boleta")
    slip = _make_boleta(db_session, batch, "transfer_slip.png", document_type="cfe_slip")

    process_boleta(db_session, boleta, FakeOCRAdapter(text=TRANSFER_BOLETA_TEXT, confidence=95.0))
    record = process_boleta(db_session, slip, FakeOCRAdapter(text=SLIP_WITHOUT_WEIGHT_TEXT, confidence=95.0))

    assert record.salida_status == "complete"
    assert record.weight_source == "estimated"
    assert record.weight == 8000.0  # WeightEstimationRule W001 (transferencia_interna)
    assert record.trip_type == "transferencia_interna"
    assert record.inventory_direction == "none"
    assert "missing_weight_no_estimate" not in record.exceptions


def test_illegible_boleta_goes_to_needs_review(db_session):
    batch = _make_batch(db_session, "batch-1")
    boleta = _make_boleta(db_session, batch, "illegible.png")

    record = process_boleta(db_session, boleta, FakeOCRAdapter(text=ILLEGIBLE_TEXT, confidence=12.0))

    assert record.status == "needs_review"
    assert record.salida_status == "boleta_only"
    assert "low_ocr_confidence" in record.exceptions
    assert any(e.startswith("missing_required_field:") for e in record.exceptions)
    assert record.confidence_score < 0.75


def test_volumen_mismatch_flags_needs_review(db_session):
    _seed_folio(db_session, "B-1003")
    batch = _make_batch(db_session, "batch-1")
    boleta_text = (
        "Folio: B-1003\n"
        "Fecha: 15/01/2026\n"
        "Centro de Explotacion: Mina San Jose\n"
        "Destino: Planta Norte\n"
        "Datos del chofer del camion: Juan Perez\n"
        "Volumen por Entregar: 9000\n"
    )
    slip_text = "Folio: B-1003\nFecha: 15/01/2026\nPeso de Entrada: 0 kg\nPeso de Salida: 7000 kg\n"
    boleta = _make_boleta(db_session, batch, "mismatch_boleta.png", document_type="boleta")
    slip = _make_boleta(db_session, batch, "mismatch_slip.png", document_type="cfe_slip")

    process_boleta(db_session, boleta, FakeOCRAdapter(text=boleta_text, confidence=95.0))
    record = process_boleta(db_session, slip, FakeOCRAdapter(text=slip_text, confidence=95.0))

    assert record.salida_status == "complete"
    assert record.status == "needs_review"
    assert "volumen_mismatch" in record.exceptions
    assert record.delivered_weight == 7000.0
    assert record.weight == 7000.0
    assert record.weight_declared == 9000.0


def test_mismatched_folios_in_same_batch_flags_both(db_session):
    _seed_folio(db_session, "B-2001")
    _seed_folio(db_session, "B-2002")
    batch = _make_batch(db_session, "batch-1")
    boleta_text = (
        "Folio: B-2001\n"
        "Fecha: 15/01/2026\n"
        "Centro de Explotacion: Mina San Jose\n"
        "Destino: Planta Norte\n"
        "Datos del chofer del camion: Juan Perez\n"
    )
    slip_text = "Folio: B-2002\nFecha: 15/01/2026\nPeso de Entrada: 500 kg\nPeso de Salida: 9500 kg\n"
    boleta = _make_boleta(db_session, batch, "mismatch_boleta2.png", document_type="boleta")
    slip = _make_boleta(db_session, batch, "mismatch_slip2.png", document_type="cfe_slip")

    boleta_record = process_boleta(db_session, boleta, FakeOCRAdapter(text=boleta_text, confidence=95.0))
    slip_record = process_boleta(db_session, slip, FakeOCRAdapter(text=slip_text, confidence=95.0))

    assert boleta_record.folio == "B-2001"
    assert slip_record.folio == "B-2002"
    assert boleta_record.salida_status != "complete"
    assert slip_record.salida_status != "complete"
    assert "salida_folio_mismatch" in slip_record.exceptions
    db_session.refresh(boleta_record)
    assert "salida_folio_mismatch" in boleta_record.exceptions
    assert boleta_record.status == "needs_review"
    assert slip_record.status == "needs_review"


def test_reprocessing_updates_existing_record_instead_of_duplicating(db_session):
    _seed_folio(db_session, "B-1001")
    batch = _make_batch(db_session, "batch-1")
    boleta = _make_boleta(db_session, batch, "clean2.png")
    adapter = FakeOCRAdapter(text=CLEAN_BOLETA_TEXT, confidence=95.0)

    first = process_boleta(db_session, boleta, adapter)
    second = process_boleta(db_session, boleta, adapter)

    assert first.id == second.id


def test_reprocessing_a_complete_pair_stays_idempotent(db_session):
    _seed_folio(db_session, "B-1001")
    batch = _make_batch(db_session, "batch-1")
    boleta = _make_boleta(db_session, batch, "clean_boleta.png", document_type="boleta")
    slip = _make_boleta(db_session, batch, "clean_slip.png", document_type="cfe_slip")
    process_boleta(db_session, boleta, FakeOCRAdapter(text=CLEAN_BOLETA_TEXT, confidence=95.0))
    first_complete = process_boleta(db_session, slip, FakeOCRAdapter(text=CLEAN_SLIP_TEXT, confidence=95.0))

    # Reprocess the secondary (slip) side: idempotent no-op, no duplicate.
    second_complete = process_boleta(db_session, slip, FakeOCRAdapter(text=CLEAN_SLIP_TEXT, confidence=95.0))
    assert second_complete.id == first_complete.id

    # Reprocess the primary (boleta) side: re-merges, still one record.
    third_complete = process_boleta(db_session, boleta, FakeOCRAdapter(text=CLEAN_BOLETA_TEXT, confidence=95.0))
    assert third_complete.id == first_complete.id
    assert third_complete.tariff_amount == 900.0
