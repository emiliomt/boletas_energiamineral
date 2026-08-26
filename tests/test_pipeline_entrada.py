"""End-to-end Entrada pipeline tests (Phase 2) -- mirrors the conventions in
tests/test_pipeline_end_to_end.py, but for kind="entrada" batches: no
origin/destination OCR'd, producer-driven classification, PricingRule-based
tariff, per-producer folio dedup, and transportista roster resolution."""
from __future__ import annotations

from app.models import Batch, Boleta, BoletaFormatTemplate, Producer
from app.pipeline.orchestrator import process_boleta
from tests.fakes import FakeOCRAdapter

# CAMAGO is a real placeholder canonical name in transportista_roster.csv
# (with alias "camago"), so this resolves cleanly without unmatched_transportista.
WITH_WEIGHT_TEXT = """\
Folio: E-2001
Fecha: 15/01/2026
Datos del chofer del camion: CAMAGO
Volumen Entregado: 50 kg
"""

NO_WEIGHT_TEXT = """\
Folio: E-3001
Fecha: 16/01/2026
Datos del chofer del camion: CAMAGO
"""

UNMATCHED_FLETERO_TEXT = """\
Folio: E-4001
Fecha: 17/01/2026
Datos del chofer del camion: NOMBRE COMPLETAMENTE DESCONOCIDO ZZZ
Volumen Entregado: 50 kg
"""


def _make_entrada_boleta(db_session, producer_id: int, filename: str, batch_label: str = "entrada-batch") -> Boleta:
    batch = Batch(label=batch_label, kind="entrada", producer_id=producer_id)
    db_session.add(batch)
    db_session.flush()
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=filename,
        stored_path=f"/tmp/{filename}",  # never opened by FakeOCRAdapter
        mime_type="image/png",
        page_number=1,
        sha256_hash=f"hash-{filename}",
    )
    db_session.add(boleta)
    db_session.flush()
    return boleta


def test_entrada_with_weight_computes_per_weight_tariff_and_inbound_inventory(db_session):
    # Real placeholder data: CTU/MINSA -> PricingRule P003 (per_weight, 120.00/ton).
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_weight.png")
    adapter = FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.kind == "entrada"
    assert record.producer_id == producer.id
    assert record.trip_type == "recepcion_compra"
    assert record.weight == 50.0
    assert record.weight_source == "measured"
    assert record.tariff_amount == 6000.0  # 120.00 * 50
    assert record.inventory_direction == "inbound"
    assert record.inventory_quantity == 50.0
    assert record.status == "auto_processed"
    assert record.exceptions == []
    # matched_tariff_rule_id must never point at a PricingRule row (the FK is
    # scoped to tariff_rules.id) -- Entradas leave it unset.
    assert record.matched_tariff_rule_id is None


def test_entrada_without_weight_uses_flat_tariff_and_estimated_inventory(db_session):
    # Real placeholder data: Bradfort -> PricingRule P002 (flat, 900.00);
    # WeightEstimationRule W003 (recepcion_compra, 10000kg) fills the gap.
    producer = db_session.query(Producer).filter_by(name="Bradfort").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_no_weight.png")
    adapter = FakeOCRAdapter(text=NO_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.tariff_amount == 900.00
    assert record.weight_source == "estimated"
    assert record.weight == 10000.0
    assert record.inventory_direction == "inbound"
    assert record.inventory_quantity == 10000.0
    assert record.status == "auto_processed"
    assert record.exceptions == []


def test_entrada_same_folio_same_producer_flags_and_needs_review(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    first_boleta = _make_entrada_boleta(db_session, producer.id, "entrada_dup1.png")
    process_boleta(db_session, first_boleta, FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0))

    second_boleta = _make_entrada_boleta(db_session, producer.id, "entrada_dup2.png")
    second_record = process_boleta(db_session, second_boleta, FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0))

    assert second_record.status == "needs_review"
    assert "folio_already_used_for_producer" in second_record.exceptions


def test_entrada_same_folio_different_producer_does_not_flag(db_session):
    producer_a = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    producer_b = db_session.query(Producer).filter_by(name="Bradfort").one()
    boleta_a = _make_entrada_boleta(db_session, producer_a.id, "entrada_prodA.png")
    process_boleta(db_session, boleta_a, FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0))

    boleta_b = _make_entrada_boleta(db_session, producer_b.id, "entrada_prodB.png")
    record_b = process_boleta(db_session, boleta_b, FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0))

    assert "folio_already_used_for_producer" not in record_b.exceptions


def test_entrada_unmatched_transportista_flags_and_needs_review(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_unmatched.png")
    adapter = FakeOCRAdapter(text=UNMATCHED_FLETERO_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.status == "needs_review"
    assert "unmatched_transportista" in record.exceptions


def test_entrada_missing_destination_is_not_flagged(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_no_dest.png")
    adapter = FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.destination is None
    assert not any(e.startswith("missing_required_field:destination") for e in record.exceptions)
    assert not any(e.startswith("missing_required_field:origin") for e in record.exceptions)


# --- Per-producer format templates (Phase 4) --------------------------------

# Genuinely different label wording from the generic system template -- see
# tests/test_field_parser.py for a direct before/after comparison against
# the universal regex set.
EXTERNO_NORTE_TEXT = """\
No. de Remision: EX-7001
Fecha: 18/01/2026
Responsable de Unidad: CAMAGO
Peso Neto Entregado: 75 kg
"""


def test_entrada_uses_producer_specific_template_for_distinct_format(db_session):
    # Real placeholder data: Proveedor Externo Norte -> BoletaFormatTemplate
    # EXTN-V1 (distinct wording) + PricingRule P005 (per_weight, 110.00/ton).
    producer = db_session.query(Producer).filter_by(name="Proveedor Externo Norte").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_externo.png")
    adapter = FakeOCRAdapter(text=EXTERNO_NORTE_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.folio == "EX-7001"
    assert record.fletero == "CAMAGO"
    assert record.weight == 75.0
    assert record.tariff_amount == 8250.0  # 110.00 * 75
    assert record.status == "auto_processed"
    assert record.exceptions == []


def test_entrada_wrong_producer_selected_cannot_parse_the_actual_format(db_session):
    # Operator mis-selected CTU/MINSA's producer/template while actually
    # scanning Proveedor Externo Norte's distinctively-worded paper -- CTU's
    # template can't read "No. de Remision"/"Responsable de Unidad", so the
    # required fields come back missing rather than silently wrong.
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_wrong_template.png")
    adapter = FakeOCRAdapter(text=EXTERNO_NORTE_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.folio is None
    assert record.fletero is None
    assert record.status == "needs_review"
    assert any(e.startswith("missing_required_field:") for e in record.exceptions)


def test_entrada_producer_without_template_falls_back_to_generic_parsing(db_session):
    # A producer with no BoletaFormatTemplate row at all degrades gracefully
    # to Phase 2's generic parse_fields() -- same real, generic label
    # wording as WITH_WEIGHT_TEXT above.
    producer = Producer(name="TEST No Template Producer", default_origin="Proveedor Externo", active=True)
    db_session.add(producer)
    db_session.flush()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_no_template.png")
    adapter = FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.folio == "E-2001"
    assert record.fletero == "CAMAGO"
    assert record.weight == 50.0


def test_entrada_expects_weight_false_but_weight_found_flags_unexpected(db_session):
    # Real placeholder data: Bradfort's template (BRAD-STD) has
    # expects_weight=false -- a weight showing up anyway is a mismatch
    # signal, not silently accepted data.
    producer = db_session.query(Producer).filter_by(name="Bradfort").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_bradfort_unexpected_weight.png")
    adapter = FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    # The found "50 kg" is never fed into tariff/inventory math as a measured
    # weight -- record.weight instead comes from WeightEstimationRule's
    # fallback (same as any other weight-absent Entrada), not the scan.
    assert record.weight_source == "estimated"
    assert record.status == "needs_review"
    assert "unexpected_weight_field" in record.exceptions


def test_entrada_inactive_template_falls_back_to_generic_parsing(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    template = db_session.query(BoletaFormatTemplate).filter_by(producer_id=producer.id).one()
    template.active = False
    db_session.flush()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_inactive_template.png")
    adapter = FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0)

    record = process_boleta(db_session, boleta, adapter)

    assert record.folio == "E-2001"
    assert record.fletero == "CAMAGO"
    assert record.weight == 50.0


def test_entrada_reprocessing_updates_existing_record_instead_of_duplicating(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()
    boleta = _make_entrada_boleta(db_session, producer.id, "entrada_reprocess.png")
    adapter = FakeOCRAdapter(text=WITH_WEIGHT_TEXT, confidence=95.0)

    first = process_boleta(db_session, boleta, adapter)
    second = process_boleta(db_session, boleta, adapter)

    assert first.id == second.id
    # Reprocessing the same boleta must not trip its own folio dedup check.
    assert "folio_already_used_for_producer" not in second.exceptions
