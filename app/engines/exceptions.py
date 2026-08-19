"""Exception evaluation: gathers exception codes from every upstream stage,
computes the composite confidence_score, checks for suspected duplicates,
and derives the final auto_processed / needs_review status.

Fully deterministic and traceable to the exception_thresholds.csv config —
no ML, no black-box scoring.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.engines.classification import ClassificationResult
from app.engines.folio_registry import FolioCheckResult
from app.engines.inventory import InventoryResult
from app.engines.tariff import TariffResult
from app.models import BoletaRecord
from app.ocr.base import OCRResult
from app.parsing.field_parser import ParsedFields
from app.rules.config_loader import get_thresholds

REQUIRED_FIELDS = ("folio", "date", "origin", "destination", "fletero")

DEFAULT_OCR_CONFIDENCE_MIN = 0.60
DEFAULT_FIELD_CONFIDENCE_MIN = 0.55
DEFAULT_AUTO_PROCESS_MIN = 0.75
DEFAULT_DUPLICATE_WINDOW_DAYS = 7
DEFAULT_VOLUMEN_MISMATCH_PCT = 10.0


@dataclass
class EvaluationResult:
    confidence_score: float
    status: str  # auto_processed | needs_review
    exceptions: list[str] = field(default_factory=list)


def threshold_float(thresholds: dict, key: str, default: float) -> float:
    t = thresholds.get(key)
    if t is None or t.threshold_value in (None, ""):
        return default
    try:
        return float(t.threshold_value)
    except ValueError:
        return default


def threshold_int(thresholds: dict, key: str, default: int) -> int:
    return int(threshold_float(thresholds, key, float(default)))


def check_duplicate(
    db: Session,
    folio: str | None,
    date: str | None,
    fletero: str | None,
    origin: str | None,
    destination: str | None,
    window_days: int,
    exclude_record_id: int | None = None,
) -> bool:
    """A boleta is a suspected duplicate if the same folio+date+fletero+route
    was already recorded within the trailing `window_days` window."""
    if not folio or not date:
        return False
    try:
        record_date = dt.date.fromisoformat(date)
    except ValueError:
        return False

    window_start = (record_date - dt.timedelta(days=window_days)).isoformat()
    window_end = (record_date + dt.timedelta(days=window_days)).isoformat()

    query = db.query(BoletaRecord).filter(
        BoletaRecord.folio == folio,
        BoletaRecord.date >= window_start,
        BoletaRecord.date <= window_end,
        BoletaRecord.fletero == fletero,
        BoletaRecord.origin == origin,
        BoletaRecord.destination == destination,
    )
    if exclude_record_id is not None:
        query = query.filter(BoletaRecord.id != exclude_record_id)
    return db.query(query.exists()).scalar()


def evaluate(
    db: Session,
    ocr: OCRResult,
    parsed: ParsedFields,
    classification: ClassificationResult,
    tariff: TariffResult,
    inventory: InventoryResult,
    is_duplicate: bool,
    folio_check: FolioCheckResult,
) -> EvaluationResult:
    thresholds = get_thresholds(db)
    ocr_confidence_min = threshold_float(thresholds, "ocr_confidence_min", DEFAULT_OCR_CONFIDENCE_MIN)
    field_confidence_min = threshold_float(
        thresholds, "field_confidence_min", DEFAULT_FIELD_CONFIDENCE_MIN
    )
    auto_process_min = threshold_float(
        thresholds, "overall_confidence_auto_process_min", DEFAULT_AUTO_PROCESS_MIN
    )

    exceptions: list[str] = []

    for field_name in REQUIRED_FIELDS:
        if getattr(parsed, field_name) is None:
            exceptions.append(f"missing_required_field:{field_name}")

    ocr_overall_confidence = ocr.confidence / 100.0
    if ocr_overall_confidence < ocr_confidence_min:
        exceptions.append("low_ocr_confidence")

    for field_name in REQUIRED_FIELDS:
        conf = parsed.field_confidences.get(field_name, 0.0)
        if getattr(parsed, field_name) is not None and conf < field_confidence_min:
            exceptions.append(f"low_field_confidence:{field_name}")

    exceptions.extend(classification.exceptions)
    exceptions.extend(tariff.exceptions)
    exceptions.extend(inventory.exceptions)
    exceptions.extend(folio_check.exceptions)

    if parsed.weight is not None and parsed.weight_declared is not None and parsed.weight_declared != 0:
        volumen_mismatch_pct = threshold_float(thresholds, "volumen_mismatch_pct", DEFAULT_VOLUMEN_MISMATCH_PCT)
        diff_pct = abs(parsed.weight - parsed.weight_declared) / parsed.weight_declared * 100
        if diff_pct > volumen_mismatch_pct:
            exceptions.append("volumen_mismatch")

    if is_duplicate:
        exceptions.append("suspected_duplicate")

    # The accuracy grade measures OCR *extraction* quality only: how well the
    # raw scan was read (ocr_overall_confidence) and how confidently the boleta
    # fields that matter were parsed (avg over REQUIRED_FIELDS). It deliberately
    # excludes optional/derived categories -- material, trip type, and the
    # downstream route/tariff/inventory resolution -- because those aren't
    # always present on the boleta (or aren't OCR'd at all), so they shouldn't
    # drag down the OCR accuracy number. When trip/route resolution fails it's
    # surfaced as its own exception (unknown_route/tariff/inventory), which
    # still routes the boleta to review without misrepresenting OCR accuracy.
    required_confidences = [parsed.field_confidences.get(f, 0.0) for f in REQUIRED_FIELDS]
    avg_field_confidence = sum(required_confidences) / len(required_confidences)

    confidence_score = 0.5 * ocr_overall_confidence + 0.5 * avg_field_confidence
    confidence_score = round(min(max(confidence_score, 0.0), 1.0), 2)

    # De-duplicate while preserving order (a field can trip more than one rule).
    exceptions = list(dict.fromkeys(exceptions))

    if exceptions:
        status = "needs_review"
    elif confidence_score >= auto_process_min:
        status = "auto_processed"
    else:
        status = "needs_review"

    return EvaluationResult(confidence_score=confidence_score, status=status, exceptions=exceptions)
