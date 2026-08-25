from __future__ import annotations

from app.engines.classification import ClassificationResult
from app.engines.exceptions import evaluate
from app.engines.folio_registry import FolioCheckResult
from app.engines.inventory import InventoryResult
from app.engines.tariff import TariffResult
from app.engines.transportista_registry import TransportistaResolution
from app.ocr.base import OCRResult
from app.parsing.field_parser import ParsedFields

# Most of these tests are about OCR/field/classification/tariff/inventory
# scoring, not folio-registry behavior — use a clean "ok" result so it never
# contributes exceptions/noise here. Folio-registry behavior itself is
# covered by tests/test_exception_folio_registry.py.
_OK_FOLIO_CHECK = FolioCheckResult(status="ok")


def _complete_parsed(**field_confidences_override) -> ParsedFields:
    confidences = {"folio": 0.9, "date": 0.9, "origin": 0.9, "destination": 0.9, "fletero": 0.9}
    confidences.update(field_confidences_override)
    return ParsedFields(
        folio="B-1",
        date="2026-01-01",
        origin="Mina San Jose",
        destination="Planta Norte",
        material="carbon",
        fletero="Juan Perez",
        weight=9000.0,
        field_confidences=confidences,
    )


def test_clean_high_confidence_boleta_is_auto_processed(db_session):
    parsed = _complete_parsed()
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type="acarreo_carbon", confidence=1.0)
    tariff = TariffResult(tariff_amount=850.0)
    inventory = InventoryResult(inventory_direction="outbound", inventory_quantity=-9000.0)

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=False, folio_check=_OK_FOLIO_CHECK)

    assert result.status == "auto_processed"
    assert result.exceptions == []
    assert result.confidence_score >= 0.75


def test_missing_required_field_forces_needs_review(db_session):
    parsed = _complete_parsed()
    parsed.destination = None
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type=None, confidence=0.0, exceptions=["unknown_route"])
    tariff = TariffResult(exceptions=["unknown_tariff"])
    inventory = InventoryResult(inventory_direction="unknown", exceptions=["unknown_inventory_direction"])

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=False, folio_check=_OK_FOLIO_CHECK)

    assert result.status == "needs_review"
    assert "missing_required_field:destination" in result.exceptions
    assert "unknown_route" in result.exceptions


def test_low_ocr_confidence_forces_needs_review_even_with_all_fields(db_session):
    parsed = _complete_parsed()
    ocr = OCRResult(text="...", confidence=20.0)  # below ocr_confidence_min
    classification = ClassificationResult(trip_type="acarreo_carbon", confidence=1.0)
    tariff = TariffResult(tariff_amount=850.0)
    inventory = InventoryResult(inventory_direction="outbound", inventory_quantity=-9000.0)

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=False, folio_check=_OK_FOLIO_CHECK)

    assert result.status == "needs_review"
    assert "low_ocr_confidence" in result.exceptions


def test_suspected_duplicate_forces_needs_review(db_session):
    parsed = _complete_parsed()
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type="acarreo_carbon", confidence=1.0)
    tariff = TariffResult(tariff_amount=850.0)
    inventory = InventoryResult(inventory_direction="outbound", inventory_quantity=-9000.0)

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=True, folio_check=_OK_FOLIO_CHECK)

    assert result.status == "needs_review"
    assert "suspected_duplicate" in result.exceptions


def test_accuracy_grade_ignores_optional_trip_resolution(db_session):
    # A boleta whose OCR read every required field well should get a HIGH
    # accuracy grade even when the route/tariff/inventory can't be resolved
    # (e.g. the route isn't in config). Trip resolution is optional to the OCR
    # accuracy grade; it still flags for review via its own exceptions.
    parsed = _complete_parsed()
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type=None, confidence=0.0, exceptions=["unknown_route"])
    tariff = TariffResult(exceptions=["unknown_tariff"])
    inventory = InventoryResult(inventory_direction="unknown", exceptions=["unknown_inventory_direction"])

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=False, folio_check=_OK_FOLIO_CHECK)

    assert result.confidence_score >= 0.9  # OCR read everything -> high accuracy grade
    assert result.status == "needs_review"  # ...but still flagged for the unresolved route
    assert "unknown_route" in result.exceptions


def test_entrada_missing_destination_does_not_flag_required_field(db_session):
    # Phase 2: Entradas never carry origin/destination -- origin is
    # satisfied by the selected Producer, destination is implicit ("our
    # patio"). Neither should trigger missing_required_field.
    parsed = _complete_parsed()
    parsed.origin = None
    parsed.destination = None
    parsed.field_confidences.pop("origin", None)
    parsed.field_confidences.pop("destination", None)
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type="recepcion_compra", confidence=1.0)
    tariff = TariffResult(tariff_amount=900.0)
    inventory = InventoryResult(inventory_direction="inbound", inventory_quantity=9000.0)

    result = evaluate(
        db_session, ocr, parsed, classification, tariff, inventory,
        is_duplicate=False, folio_check=_OK_FOLIO_CHECK, kind="entrada",
    )

    assert "missing_required_field:destination" not in result.exceptions
    assert "missing_required_field:origin" not in result.exceptions
    assert result.status == "auto_processed"
    assert result.exceptions == []


def test_salida_missing_destination_still_flags_required_field(db_session):
    # Default kind="salida" preserves today's behavior exactly.
    parsed = _complete_parsed()
    parsed.destination = None
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type=None, confidence=0.0, exceptions=["unknown_route"])
    tariff = TariffResult(exceptions=["unknown_tariff"])
    inventory = InventoryResult(inventory_direction="unknown", exceptions=["unknown_inventory_direction"])

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=False, folio_check=_OK_FOLIO_CHECK)

    assert "missing_required_field:destination" in result.exceptions


def test_entrada_unmatched_transportista_flags_and_forces_review(db_session):
    parsed = _complete_parsed()
    ocr = OCRResult(text="...", confidence=95.0)
    classification = ClassificationResult(trip_type="recepcion_compra", confidence=1.0)
    tariff = TariffResult(tariff_amount=900.0)
    inventory = InventoryResult(inventory_direction="inbound", inventory_quantity=9000.0)
    transportista = TransportistaResolution(exceptions=["unmatched_transportista"])

    result = evaluate(
        db_session, ocr, parsed, classification, tariff, inventory,
        is_duplicate=False, folio_check=_OK_FOLIO_CHECK, kind="entrada", transportista=transportista,
    )

    assert result.status == "needs_review"
    assert "unmatched_transportista" in result.exceptions


def test_confidence_score_below_threshold_needs_review_despite_no_exceptions(db_session):
    # Every input individually clears its own hard-block/soft-flag checks,
    # but the composite score still lands under the auto-process gate.
    parsed = _complete_parsed(folio=0.6, date=0.6, origin=0.6, destination=0.6, fletero=0.6)
    ocr = OCRResult(text="...", confidence=61.0)  # just above ocr_confidence_min (0.60)
    classification = ClassificationResult(trip_type="acarreo_carbon", confidence=0.55)
    tariff = TariffResult(tariff_amount=850.0)
    inventory = InventoryResult(inventory_direction="outbound", inventory_quantity=-9000.0)

    result = evaluate(db_session, ocr, parsed, classification, tariff, inventory, is_duplicate=False, folio_check=_OK_FOLIO_CHECK)

    assert result.exceptions == []
    assert result.confidence_score < 0.75
    assert result.status == "needs_review"
