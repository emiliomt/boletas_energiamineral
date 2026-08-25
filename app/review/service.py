"""Applies a human reviewer's field corrections and/or approval to a
BoletaRecord: writes a ReviewAudit row per changed field, re-runs
classification/tariff/inventory as needed, and recomputes
confidence_score/status/exceptions from the corrected values.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.engines.classification import ClassificationResult, classify_trip
from app.engines.exceptions import (
    DEFAULT_DUPLICATE_WINDOW_DAYS,
    check_duplicate,
    evaluate,
    threshold_int,
)
from app.engines.folio_registry import check_folio, link_folio, unlink_folio
from app.engines.inventory import InventoryResult
from app.engines.tariff import compute_tariff
from app.models import BoletaRecord, Folio, ReviewAudit
from app.ocr.base import OCRResult
from app.parsing.field_parser import ParsedFields
from app.rules.config_loader import get_active_routes, get_thresholds
from app.schemas import ReviewCorrection

# Fields a reviewer can edit; "trip_type" is assigned directly rather than
# re-derived, per the PDR's "assign trip type" review capability.
EDITABLE_FIELDS = (
    "folio",
    "date",
    "origin",
    "destination",
    "material",
    "fletero",
    "weight",
    "trip_type",
    "secondary_origin",
    "contract_number",
    "truck_box_number",
    "weight_declared",
    "proveedor",
    "concesion_minera",
    "representante_legal",
)
# Subset that field_parser also tracks a per-field OCR confidence for.
_PARSED_TRACKED_FIELDS = ("folio", "date", "origin", "destination", "material", "fletero", "weight")
# Coal-quality metrics live in BoletaRecord.quality_data (a JSON dict) rather
# than dedicated columns, so they're corrected separately from EDITABLE_FIELDS.
_QUALITY_FIELDS = ("poder_calorifico_superior", "humedad_pct", "ceniza_pct", "azufre_pct", "fsi", "granulometria")


def _find_route_rule_for_trip_type(db: Session, trip_type: str | None, origin: str | None, destination: str | None):
    if not trip_type:
        return None
    candidates = [r for r in get_active_routes(db) if r.trip_type == trip_type]
    if not candidates:
        return None
    if origin and destination:
        for r in candidates:
            if r.origin.strip().lower() == origin.strip().lower() and r.destination.strip().lower() == destination.strip().lower():
                return r
    return candidates[0]


def apply_review(db: Session, record: BoletaRecord, correction: ReviewCorrection) -> BoletaRecord:
    field_confidences = dict(record.field_confidences or {})
    changed_fields: list[str] = []
    old_folio = record.folio

    for field_name in EDITABLE_FIELDS:
        new_value = getattr(correction, field_name)
        if new_value is None:
            continue
        old_value = getattr(record, field_name)
        if old_value == new_value:
            continue
        db.add(
            ReviewAudit(
                boleta_record_id=record.id,
                field_name=field_name,
                old_value=None if old_value is None else str(old_value),
                new_value=str(new_value),
                action="correction",
                edited_by=correction.edited_by,
                note=correction.note,
            )
        )
        setattr(record, field_name, new_value)
        changed_fields.append(field_name)
        if field_name in _PARSED_TRACKED_FIELDS:
            field_confidences[field_name] = 1.0  # human-verified, full confidence

    # Coal-quality metrics are stored in the quality_data JSON dict.
    quality = dict(record.quality_data or {})
    for quality_field in _QUALITY_FIELDS:
        new_value = getattr(correction, quality_field)
        if new_value is None:
            continue
        old_value = quality.get(quality_field)
        if old_value == new_value:
            continue
        db.add(
            ReviewAudit(
                boleta_record_id=record.id,
                field_name=f"quality:{quality_field}",
                old_value=None if old_value is None else str(old_value),
                new_value=str(new_value),
                action="correction",
                edited_by=correction.edited_by,
                note=correction.note,
            )
        )
        quality[quality_field] = new_value
    record.quality_data = quality

    trip_type_assigned_explicitly = correction.trip_type is not None
    route_fields_changed = "origin" in changed_fields or "destination" in changed_fields

    if not trip_type_assigned_explicitly and route_fields_changed:
        classification = classify_trip(db, record.origin, record.destination)
        record.trip_type = classification.trip_type
        matched_rule = classification.matched_rule
    else:
        matched_rule = _find_route_rule_for_trip_type(db, record.trip_type, record.origin, record.destination)
        exceptions = [] if matched_rule or not record.trip_type else ["unknown_route"]
        classification = ClassificationResult(
            trip_type=record.trip_type,
            confidence=1.0 if matched_rule else 0.0,
            matched_rule=matched_rule,
            exceptions=exceptions,
        )

    distance_band = matched_rule.distance_band if matched_rule else None
    tariff = compute_tariff(db, record.trip_type, distance_band)

    direction = matched_rule.inventory_direction if matched_rule else (record.inventory_direction or "unknown")
    if correction.weight is not None:
        record.weight_source = "measured"

    inventory_exceptions: list[str] = []
    quantity: float | None = None
    if direction == "unknown":
        inventory_exceptions.append("unknown_inventory_direction")
    elif direction == "inbound":
        quantity = record.weight
    elif direction == "outbound":
        quantity = -record.weight if record.weight is not None else None
    if record.weight is None and direction in ("inbound", "outbound"):
        inventory_exceptions.append("missing_weight_no_estimate")

    record.inventory_direction = direction
    record.inventory_quantity = quantity
    record.tariff_amount = tariff.tariff_amount
    record.matched_route_rule_id = matched_rule.id if matched_rule else None
    record.matched_tariff_rule_id = tariff.matched_rule.id if tariff.matched_rule else None
    record.field_confidences = field_confidences

    inventory_result = InventoryResult(
        inventory_direction=direction,
        inventory_quantity=quantity,
        weight=record.weight,
        weight_source=record.weight_source,
        matched_weight_rule=None,
        exceptions=inventory_exceptions,
    )
    parsed = ParsedFields(
        folio=record.folio,
        date=record.date,
        origin=record.origin,
        secondary_origin=record.secondary_origin,
        destination=record.destination,
        contract_number=record.contract_number,
        material=record.material,
        fletero=record.fletero,
        truck_box_number=record.truck_box_number,
        weight=record.weight,
        weight_declared=record.weight_declared,
        field_confidences=field_confidences,
    )
    ocr = OCRResult(text=record.ocr_text or "", confidence=(record.ocr_confidence or 0.0) * 100)

    # A reviewer can hand-correct the folio, so a manual edit can't silently
    # bypass the issued-folio registry: unlink the old folio (if it was
    # linked to this record) and re-check/link the corrected one.
    if "folio" in changed_fields and old_folio:
        old_folio_row = db.query(Folio).filter_by(folio=old_folio).one_or_none()
        if old_folio_row is not None and old_folio_row.boleta_record_id == record.id:
            unlink_folio(old_folio_row)

    folio_check = check_folio(db, record.folio, exclude_record_id=record.id)
    if folio_check.status == "ok" and folio_check.folio_row is not None:
        link_folio(folio_check.folio_row, record.id)

    thresholds = get_thresholds(db)
    window_days = threshold_int(thresholds, "duplicate_check_window_days", DEFAULT_DUPLICATE_WINDOW_DAYS)
    is_duplicate = check_duplicate(
        db,
        folio=record.folio,
        date=record.date,
        fletero=record.fletero,
        origin=record.origin,
        destination=record.destination,
        window_days=window_days,
        exclude_record_id=record.id,
    )

    evaluation = evaluate(db, ocr, parsed, classification, tariff, inventory_result, is_duplicate, folio_check)
    record.confidence_score = evaluation.confidence_score
    record.exceptions = evaluation.exceptions

    if correction.action == "approve":
        old_status = record.status
        record.status = "auto_processed"
        db.add(
            ReviewAudit(
                boleta_record_id=record.id,
                field_name="__status__",
                old_value=old_status,
                new_value="auto_processed",
                action="approval",
                edited_by=correction.edited_by,
                note=correction.note,
            )
        )
    else:
        record.status = evaluation.status

    db.flush()
    return record
