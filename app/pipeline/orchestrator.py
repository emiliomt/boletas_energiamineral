"""The single place that wires every pipeline stage together for one boleta:
OCR -> parse -> classify -> tariff -> inventory -> exceptions/confidence ->
persist. Read this file to understand the whole flow end-to-end.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.engines.classification import classify_trip
from app.engines.exceptions import (
    DEFAULT_DUPLICATE_WINDOW_DAYS,
    check_duplicate,
    evaluate,
    threshold_int,
)
from app.engines.inventory import compute_inventory
from app.engines.tariff import compute_tariff
from app.models import Boleta, BoletaRecord
from app.ocr.base import OCRAdapter
from app.parsing.field_parser import parse_fields
from app.rules.config_loader import get_thresholds


def process_boleta(db: Session, boleta: Boleta, ocr_adapter: OCRAdapter) -> BoletaRecord:
    """Runs the full pipeline for one already-stored Boleta and persists (or
    updates, on reprocess) its BoletaRecord. Returns the record."""

    ocr_result = ocr_adapter.extract(Path(boleta.stored_path))
    parsed = parse_fields(ocr_result)

    classification = classify_trip(db, parsed.origin, parsed.destination)
    distance_band = classification.matched_rule.distance_band if classification.matched_rule else None
    tariff = compute_tariff(db, classification.trip_type, distance_band)
    inventory = compute_inventory(db, classification.matched_rule, parsed.material, parsed.weight)

    thresholds = get_thresholds(db)
    window_days = threshold_int(thresholds, "duplicate_check_window_days", DEFAULT_DUPLICATE_WINDOW_DAYS)
    existing_record = db.query(BoletaRecord).filter_by(boleta_id=boleta.id).one_or_none()
    is_duplicate = check_duplicate(
        db,
        folio=parsed.folio,
        date=parsed.date,
        fletero=parsed.fletero,
        origin=parsed.origin,
        destination=parsed.destination,
        window_days=window_days,
        exclude_record_id=existing_record.id if existing_record else None,
    )

    evaluation = evaluate(db, ocr_result, parsed, classification, tariff, inventory, is_duplicate)

    record = existing_record or BoletaRecord(boleta_id=boleta.id)
    record.ocr_text = ocr_result.text
    record.ocr_confidence = round(ocr_result.confidence / 100.0, 3)
    record.folio = parsed.folio
    record.date = parsed.date
    record.origin = parsed.origin
    record.destination = parsed.destination
    record.material = parsed.material
    record.fletero = parsed.fletero
    record.weight = inventory.weight
    record.weight_source = inventory.weight_source
    record.trip_type = classification.trip_type
    record.tariff_amount = tariff.tariff_amount
    record.inventory_direction = inventory.inventory_direction
    record.inventory_quantity = inventory.inventory_quantity
    record.confidence_score = evaluation.confidence_score
    record.status = evaluation.status
    record.exceptions = evaluation.exceptions
    record.field_confidences = parsed.field_confidences
    record.matched_route_rule_id = classification.matched_rule.id if classification.matched_rule else None
    record.matched_tariff_rule_id = tariff.matched_rule.id if tariff.matched_rule else None
    record.matched_weight_rule_id = inventory.matched_weight_rule.id if inventory.matched_weight_rule else None

    if not existing_record:
        db.add(record)
    db.flush()
    return record
