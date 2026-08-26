"""The single place that wires every pipeline stage together for one boleta:
QR decode -> OCR -> parse -> classify -> tariff -> inventory -> folio
registry check -> exceptions/confidence -> persist. Read this file to
understand the whole flow end-to-end.

`kind` (entrada|salida) comes from the boleta's Batch/lote -- selected once
at lote-creation time (Phase 2), not re-entered per file. Within kind=salida,
`document_type` (boleta|cfe_slip, Phase 3) further splits the flow: a Salida
boleta and its CFE weight slip are two separate scans sharing a folio, and
neither can price/post inventory alone -- see _process_salida_boleta /
_process_salida_cfe_slip / _complete_salida below, and
app/engines/salida_reconciliation.py for the matching logic. Entrada stays
exactly as Phase 2 left it (_process_entrada).
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.engines.classification import ClassificationResult, classify_entrada, classify_trip
from app.engines.exceptions import (
    DEFAULT_DUPLICATE_WINDOW_DAYS,
    SALIDA_CFE_SLIP_REQUIRED_FIELDS,
    check_duplicate,
    evaluate,
    threshold_int,
)
from app.engines.folio_registry import (
    FolioCheckResult,
    check_entrada_folio,
    check_folio,
    link_folio,
)
from app.engines.inventory import InventoryResult, compute_inventory
from app.engines.salida_reconciliation import compute_delivered_weight, find_salida_counterpart
from app.engines.tariff import TariffResult, compute_entrada_tariff, compute_salida_tariff
from app.engines.transportista_registry import resolve_transportista
from app.models import Boleta, BoletaRecord, Producer
from app.ocr.base import OCRAdapter, OCRResult
from app.ocr.qr_decoder import decode_qr_folio
from app.parsing.field_parser import (
    CfeSlipFields,
    ParsedFields,
    parse_cfe_slip_fields,
    parse_fields,
    parse_fields_with_template,
)
from app.rules.config_loader import get_active_template_for_producer, get_thresholds


def process_boleta(db: Session, boleta: Boleta, ocr_adapter: OCRAdapter) -> BoletaRecord:
    """Runs the full pipeline for one already-stored Boleta and persists (or
    updates, on reprocess) its BoletaRecord. Returns the record that carries
    the outcome -- for a Salida document that completes a pairing, that's
    the *primary* record (the one the counterpart merged into), not
    necessarily the row keyed by this exact boleta_id."""

    kind = boleta.batch.kind if boleta.batch else "salida"
    producer_id = boleta.batch.producer_id if boleta.batch else None
    producer = db.get(Producer, producer_id) if (kind == "entrada" and producer_id is not None) else None
    document_type = boleta.document_type or "boleta"

    existing_record = db.query(BoletaRecord).filter_by(boleta_id=boleta.id).one_or_none()

    if kind == "salida" and existing_record is not None and existing_record.reconciled_with_record_id is not None:
        # This document was already merged into a primary earlier -- the
        # pairing is fixed, hand back the primary unchanged rather than
        # re-searching (idempotent no-op).
        primary = db.get(BoletaRecord, existing_record.reconciled_with_record_id)
        if primary is not None:
            return primary

    image_path = Path(boleta.stored_path)
    qr_folio = decode_qr_folio(image_path)
    ocr_result = ocr_adapter.extract(image_path)

    if kind == "entrada":
        return _process_entrada(db, boleta, ocr_result, qr_folio, producer, producer_id, existing_record)
    if document_type == "cfe_slip":
        return _process_salida_cfe_slip(db, boleta, ocr_result, qr_folio, existing_record)
    return _process_salida_boleta(db, boleta, ocr_result, qr_folio, existing_record)


def _process_entrada(
    db: Session,
    boleta: Boleta,
    ocr_result: OCRResult,
    qr_folio: str | None,
    producer: Producer | None,
    producer_id: int | None,
    existing_record: BoletaRecord | None,
) -> BoletaRecord:
    """Phase 4: uses this producer's BoletaFormatTemplate (if one is
    configured) instead of the generic parse_fields() -- see
    app/parsing/field_parser.py's parse_fields_with_template(). A producer
    with no active template degrades to exactly the Phase 2 behavior below,
    unchanged."""
    template = get_active_template_for_producer(db, producer_id)
    parsed = parse_fields_with_template(ocr_result, template) if template is not None else parse_fields(ocr_result)
    if qr_folio:
        parsed.folio = qr_folio
        parsed.field_confidences["folio"] = 1.0

    classification = classify_entrada(db, producer)
    transportista = resolve_transportista(db, parsed.fletero)
    tariff = compute_entrada_tariff(db, producer, parsed.weight)
    inventory = compute_inventory(db, classification.matched_rule, parsed.material, parsed.weight)

    # Folio numbers are unique per-producer, not globally, and there's no
    # pre-issued registry for a producer's own paper -- dedup against
    # previously ingested Entrada records instead, scoped to
    # (producer_id, folio); supersedes the generic duplicate heuristic,
    # which would over-trigger since every Entrada from one producer shares
    # the same producer-derived origin.
    is_duplicate = False
    folio_check = check_entrada_folio(
        db, producer_id, parsed.folio, exclude_record_id=existing_record.id if existing_record else None
    )

    evaluation = evaluate(
        db, ocr_result, parsed, classification, tariff, inventory, is_duplicate, folio_check,
        kind="entrada", transportista=transportista,
    )

    record = existing_record or BoletaRecord(boleta_id=boleta.id)
    record.kind = "entrada"
    record.producer_id = producer_id
    record.ocr_text = ocr_result.text
    record.ocr_confidence = round(ocr_result.confidence / 100.0, 3)
    record.ocr_engine = ocr_result.engine
    record.folio = parsed.folio
    record.date = parsed.date
    record.origin = parsed.origin
    record.secondary_origin = parsed.secondary_origin
    record.destination = parsed.destination
    record.contract_number = parsed.contract_number
    record.material = parsed.material
    record.fletero = parsed.fletero
    record.truck_box_number = parsed.truck_box_number
    record.proveedor = parsed.proveedor
    record.concesion_minera = parsed.concesion_minera
    record.representante_legal = parsed.representante_legal
    record.weight = inventory.weight
    record.weight_declared = parsed.weight_declared
    record.weight_source = inventory.weight_source
    record.quality_data = parsed.quality_data
    record.trip_type = classification.trip_type
    record.tariff_amount = tariff.tariff_amount
    record.inventory_direction = inventory.inventory_direction
    record.inventory_quantity = inventory.inventory_quantity
    record.confidence_score = evaluation.confidence_score
    record.status = evaluation.status
    record.exceptions = evaluation.exceptions
    record.field_confidences = parsed.field_confidences
    record.matched_route_rule_id = classification.matched_rule.id if classification.matched_rule else None
    # matched_tariff_rule_id's FK is scoped to tariff_rules.id -- an Entrada's
    # matched pricing rule lives in the separate pricing_rules table, so it
    # must never be written here (a PricingRule.id could collide with an
    # unrelated TariffRule.id and silently corrupt this reference).
    record.matched_tariff_rule_id = None
    record.matched_weight_rule_id = inventory.matched_weight_rule.id if inventory.matched_weight_rule else None

    if not existing_record:
        db.add(record)
    db.flush()

    if folio_check.status == "ok" and folio_check.folio_row is not None:
        link_folio(folio_check.folio_row, record.id)

    return record


def _document_type_mismatch_exceptions(document_type: str, folio_found: bool, has_type_specific_fields: bool) -> list[str]:
    """Risk mitigation (PRD Phase 3 §9): if an operator mis-tags a document
    (calls a CFE slip a "boleta" or vice versa), the type-specific parser
    will find nothing to extract. Rather than silently persisting an
    empty-fielded record, flag it -- but only when the folio itself *was*
    legible (so this is about a wrong tag, not just a poor scan, which
    already gets low_ocr_confidence/missing_required_field on its own)."""
    if folio_found and not has_type_specific_fields:
        return ["document_type_mismatch"]
    return []


def _flag_salida_folio_mismatch(sibling: BoletaRecord) -> None:
    """Retroactively flags a pre-existing partial record whose same-batch
    counterpart just arrived with a different folio, so both halves of a
    mis-paired upload are visible in the queue, not just the one being
    processed right now."""
    if "salida_folio_mismatch" not in (sibling.exceptions or []):
        sibling.exceptions = [*(sibling.exceptions or []), "salida_folio_mismatch"]
    sibling.status = "needs_review"


def _process_salida_boleta(
    db: Session, boleta: Boleta, ocr_result: OCRResult, qr_folio: str | None, existing_record: BoletaRecord | None
) -> BoletaRecord:
    parsed = parse_fields(ocr_result)
    if qr_folio:
        parsed.folio = qr_folio
        parsed.field_confidences["folio"] = 1.0

    if existing_record is not None and existing_record.salida_status == "complete":
        # Reprocessing the boleta side of an already-complete pair: re-merge
        # fresh boleta data with the CFE-side data already stored on this
        # same (primary) record from the earlier merge -- no re-search.
        boleta_ocr = (ocr_result.text, ocr_result.confidence, ocr_result.engine)
        slip_ocr = (existing_record.ocr_text, (existing_record.ocr_confidence or 0.0) * 100.0, existing_record.ocr_engine)
        return _complete_salida(db, existing_record, parsed, existing_record, boleta_ocr, slip_ocr)

    reconciliation = find_salida_counterpart(
        db, boleta.batch_id, parsed.folio, "boleta",
        exclude_record_id=existing_record.id if existing_record else None,
    )
    if reconciliation.mismatched_sibling is not None:
        _flag_salida_folio_mismatch(reconciliation.mismatched_sibling)

    record = existing_record or BoletaRecord(boleta_id=boleta.id)
    record.kind = "salida"
    record.producer_id = None
    record.ocr_text = ocr_result.text
    record.ocr_confidence = round(ocr_result.confidence / 100.0, 3)
    record.ocr_engine = ocr_result.engine
    record.folio = parsed.folio
    record.date = parsed.date
    record.origin = parsed.origin
    record.secondary_origin = parsed.secondary_origin
    record.destination = parsed.destination
    record.contract_number = parsed.contract_number
    record.material = parsed.material
    record.fletero = parsed.fletero
    record.truck_box_number = parsed.truck_box_number
    record.proveedor = parsed.proveedor
    record.concesion_minera = parsed.concesion_minera
    record.representante_legal = parsed.representante_legal
    record.weight_declared = parsed.weight_declared
    record.quality_data = parsed.quality_data
    record.field_confidences = parsed.field_confidences
    record.salida_status = "boleta_only"

    if not existing_record:
        db.add(record)
    db.flush()

    if reconciliation.salida_status != "complete":
        # Classification doesn't need the CFE side, so it's safe (and
        # useful for visibility) to compute even while waiting -- but
        # tariff/inventory genuinely can't run without delivered_weight,
        # so those stay unset (PRD Phase 3 §6.5: no partial/guessed
        # inventory posting).
        classification = classify_trip(db, parsed.origin, parsed.destination)
        record.trip_type = classification.trip_type
        record.matched_route_rule_id = classification.matched_rule.id if classification.matched_rule else None

        reconciliation.exceptions.extend(
            _document_type_mismatch_exceptions(
                "boleta",
                folio_found=parsed.folio is not None,
                has_type_specific_fields=bool(parsed.origin or parsed.destination or parsed.fletero),
            )
        )

        thresholds = get_thresholds(db)
        window_days = threshold_int(thresholds, "duplicate_check_window_days", DEFAULT_DUPLICATE_WINDOW_DAYS)
        is_duplicate = check_duplicate(
            db, folio=parsed.folio, date=parsed.date, fletero=parsed.fletero,
            origin=parsed.origin, destination=parsed.destination, window_days=window_days,
            exclude_record_id=record.id,
        )
        folio_check = check_folio(db, parsed.folio, exclude_record_id=record.id)

        evaluation = evaluate(
            db, ocr_result, parsed, classification, TariffResult(), InventoryResult(),
            is_duplicate, folio_check, kind="salida", reconciliation=reconciliation,
        )
        record.confidence_score = evaluation.confidence_score
        record.exceptions = evaluation.exceptions
        # A partial record is never "done" -- auto_processed would wrongly
        # suggest it's ready to pay/count. See PRD Phase 3 §6.6.
        record.status = "needs_review"
        db.flush()

        if folio_check.status == "ok" and folio_check.folio_row is not None:
            link_folio(folio_check.folio_row, record.id)

        return record

    primary = reconciliation.counterpart_record
    assert primary is not None  # guaranteed by salida_status == "complete"
    boleta_ocr = (ocr_result.text, ocr_result.confidence, ocr_result.engine)
    slip_ocr = (primary.ocr_text, (primary.ocr_confidence or 0.0) * 100.0, primary.ocr_engine)
    merged = _complete_salida(db, primary, parsed, primary, boleta_ocr, slip_ocr)
    record.reconciled_with_record_id = merged.id
    db.flush()
    return merged


def _process_salida_cfe_slip(
    db: Session, boleta: Boleta, ocr_result: OCRResult, qr_folio: str | None, existing_record: BoletaRecord | None
) -> BoletaRecord:
    parsed = parse_cfe_slip_fields(ocr_result)
    if qr_folio:
        parsed.folio = qr_folio
        parsed.field_confidences["folio"] = 1.0

    if existing_record is not None and existing_record.salida_status == "complete":
        slip_ocr = (ocr_result.text, ocr_result.confidence, ocr_result.engine)
        boleta_ocr = (existing_record.ocr_text, (existing_record.ocr_confidence or 0.0) * 100.0, existing_record.ocr_engine)
        return _complete_salida(db, existing_record, existing_record, parsed, boleta_ocr, slip_ocr)

    reconciliation = find_salida_counterpart(
        db, boleta.batch_id, parsed.folio, "cfe_slip",
        exclude_record_id=existing_record.id if existing_record else None,
    )
    if reconciliation.mismatched_sibling is not None:
        _flag_salida_folio_mismatch(reconciliation.mismatched_sibling)

    record = existing_record or BoletaRecord(boleta_id=boleta.id)
    record.kind = "salida"
    record.producer_id = None
    record.ocr_text = ocr_result.text
    record.ocr_confidence = round(ocr_result.confidence / 100.0, 3)
    record.ocr_engine = ocr_result.engine
    record.folio = parsed.folio
    record.date = parsed.date
    record.cfe_entry_weight = parsed.cfe_entry_weight
    record.cfe_exit_weight = parsed.cfe_exit_weight
    record.delivered_weight = compute_delivered_weight(parsed.cfe_entry_weight, parsed.cfe_exit_weight)
    record.field_confidences = parsed.field_confidences
    record.salida_status = "cfe_slip_only"

    if not existing_record:
        db.add(record)
    db.flush()

    if reconciliation.salida_status != "complete":
        reconciliation.exceptions.extend(
            _document_type_mismatch_exceptions(
                "cfe_slip",
                folio_found=parsed.folio is not None,
                has_type_specific_fields=parsed.cfe_entry_weight is not None or parsed.cfe_exit_weight is not None,
            )
        )
        # Not our pre-issued registry (CFE's own numbering) and no
        # meaningful "duplicate CFE slip" concept defined -- skip
        # check_folio/check_duplicate for the slip side.
        evaluation = evaluate(
            db, ocr_result, _cfe_as_parsed_fields(parsed), ClassificationResult(), TariffResult(), InventoryResult(),
            is_duplicate=False, folio_check=FolioCheckResult(status="ok"),
            kind="salida", required_fields_override=SALIDA_CFE_SLIP_REQUIRED_FIELDS, reconciliation=reconciliation,
        )
        record.confidence_score = evaluation.confidence_score
        record.exceptions = evaluation.exceptions
        record.status = "needs_review"
        db.flush()
        return record

    primary = reconciliation.counterpart_record
    assert primary is not None  # guaranteed by salida_status == "complete"
    slip_ocr = (ocr_result.text, ocr_result.confidence, ocr_result.engine)
    boleta_ocr = (primary.ocr_text, (primary.ocr_confidence or 0.0) * 100.0, primary.ocr_engine)
    merged = _complete_salida(db, primary, primary, parsed, boleta_ocr, slip_ocr)
    record.reconciled_with_record_id = merged.id
    db.flush()
    return merged


def _cfe_as_parsed_fields(cfe: CfeSlipFields) -> ParsedFields:
    """Adapts a CfeSlipFields into the shared ParsedFields carrier so the
    slip's partial-state pass can reuse evaluate() unchanged (it does
    generic getattr(parsed, field_name) lookups, plus a fixed
    parsed.weight/parsed.weight_declared access for the volumen_mismatch
    check -- both default to None on ParsedFields, which is exactly right
    for a slip that has neither)."""
    return ParsedFields(folio=cfe.folio, date=cfe.date, field_confidences=dict(cfe.field_confidences))


def _complete_salida(
    db: Session,
    primary: BoletaRecord,
    boleta_source,
    slip_source,
    boleta_ocr: tuple[str | None, float, str | None],
    slip_ocr: tuple[str | None, float, str | None],
) -> BoletaRecord:
    """Merges both sides of a reconciled Salida pair onto `primary` and
    computes tariff/inventory now that delivered_weight is known.

    `boleta_source`/`slip_source` are each either a freshly parsed
    ParsedFields/CfeSlipFields (whichever document is being processed right
    now) or the counterpart BoletaRecord that already holds that side's
    data from an earlier pass -- both expose matching attribute names
    (folio, date, origin, cfe_entry_weight, ...), so plain getattr works
    regardless of which kind of object it is.

    `boleta_ocr`/`slip_ocr` are (text, confidence_0_100, engine) snapshots
    for each side, used only for the primary's own OCR audit fields.
    """

    def g(source, name, default=None):
        return getattr(source, name, default)

    folio = g(boleta_source, "folio") or g(slip_source, "folio")
    date = g(boleta_source, "date") or g(slip_source, "date")
    origin = g(boleta_source, "origin")
    destination = g(boleta_source, "destination")
    material = g(boleta_source, "material")
    fletero = g(boleta_source, "fletero")

    cfe_entry_weight = g(slip_source, "cfe_entry_weight")
    cfe_exit_weight = g(slip_source, "cfe_exit_weight")
    delivered_weight = compute_delivered_weight(cfe_entry_weight, cfe_exit_weight)

    merged_field_confidences = dict(g(slip_source, "field_confidences", {}) or {})
    merged_field_confidences.update(g(boleta_source, "field_confidences", {}) or {})

    classification = classify_trip(db, origin, destination)
    tariff = compute_salida_tariff(db, origin, delivered_weight)
    inventory = compute_inventory(db, classification.matched_rule, material, delivered_weight)

    thresholds = get_thresholds(db)
    window_days = threshold_int(thresholds, "duplicate_check_window_days", DEFAULT_DUPLICATE_WINDOW_DAYS)
    is_duplicate = check_duplicate(
        db, folio=folio, date=date, fletero=fletero, origin=origin, destination=destination,
        window_days=window_days, exclude_record_id=primary.id,
    )
    folio_check = check_folio(db, folio, exclude_record_id=primary.id)

    parsed_for_eval = ParsedFields(
        folio=folio, date=date, origin=origin, destination=destination, material=material, fletero=fletero,
        weight=delivered_weight, weight_declared=g(boleta_source, "weight_declared"),
        field_confidences=merged_field_confidences,
    )
    ocr_for_eval = OCRResult(text=boleta_ocr[0] or "", confidence=boleta_ocr[1])

    evaluation = evaluate(
        db, ocr_for_eval, parsed_for_eval, classification, tariff, inventory, is_duplicate, folio_check, kind="salida"
    )

    primary.kind = "salida"
    primary.producer_id = None
    primary.salida_status = "complete"
    primary.folio = folio
    primary.date = date
    primary.origin = origin
    primary.secondary_origin = g(boleta_source, "secondary_origin")
    primary.destination = destination
    primary.contract_number = g(boleta_source, "contract_number")
    primary.material = material
    primary.fletero = fletero
    primary.truck_box_number = g(boleta_source, "truck_box_number")
    primary.proveedor = g(boleta_source, "proveedor")
    primary.concesion_minera = g(boleta_source, "concesion_minera")
    primary.representante_legal = g(boleta_source, "representante_legal")
    # delivered_weight replaces the generic weight field's role for Salida
    # (PRD Phase 3 §5.1) -- weight/weight_source stay populated too so
    # existing display/export code keeps showing something meaningful.
    # Read from `inventory` (not `delivered_weight` directly) so a missing
    # CFE weight still falls back through WeightEstimationRule correctly.
    primary.weight = inventory.weight
    primary.weight_declared = g(boleta_source, "weight_declared")
    primary.weight_source = inventory.weight_source
    primary.quality_data = g(boleta_source, "quality_data", {}) or {}
    primary.cfe_entry_weight = cfe_entry_weight
    primary.cfe_exit_weight = cfe_exit_weight
    primary.delivered_weight = delivered_weight
    primary.trip_type = classification.trip_type
    primary.tariff_amount = tariff.tariff_amount
    primary.inventory_direction = inventory.inventory_direction
    primary.inventory_quantity = inventory.inventory_quantity
    primary.confidence_score = evaluation.confidence_score
    primary.status = evaluation.status
    primary.exceptions = evaluation.exceptions
    primary.field_confidences = merged_field_confidences
    primary.matched_route_rule_id = classification.matched_rule.id if classification.matched_rule else None
    # matched_tariff_rule_id's FK is scoped to tariff_rules.id -- Salida's
    # matched pricing rule (Phase 3) lives in the separate pricing_rules
    # table now, same FK-safety reasoning as Phase 2's Entrada tariff.
    primary.matched_tariff_rule_id = None
    primary.matched_weight_rule_id = inventory.matched_weight_rule.id if inventory.matched_weight_rule else None
    # Concatenated for audit visibility -- not parsed back out, so a small
    # amount of re-nesting on repeated reprocessing is a cosmetic cost only.
    primary.ocr_text = f"[BOLETA]\n{boleta_ocr[0] or ''}\n\n[CFE SLIP]\n{slip_ocr[0] or ''}"
    primary.ocr_confidence = round(min(boleta_ocr[1], slip_ocr[1]) / 100.0, 3)
    primary.ocr_engine = boleta_ocr[2] or slip_ocr[2]

    db.flush()

    if folio_check.status == "ok" and folio_check.folio_row is not None:
        link_folio(folio_check.folio_row, primary.id)

    return primary
