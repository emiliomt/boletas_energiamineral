"""Rule-based field extraction from raw OCR text.

Labels match the real "Reporte de Calidad y Origen del Carbón" boleta
(the client's actual pre-printed form / the replacement template this
system now prints for the vendor -- see app/qr/batch_pdf.py). A field
that isn't found gets confidence 0 and is left None so the exception
evaluator can flag it. Quality metrics (poder calorífico, % humedad, etc.)
are captured best-effort into `quality_data` for provenance only -- they
don't drive tariff/inventory math, so a miss there never blocks
auto-processing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ocr.base import OCRResult
from app.parsing.normalizers import clean_text, parse_date, parse_weight_kg

# Each field: ordered list of label regexes tried in turn; first match wins.
# Accents are made optional (e.g. "explotaci[oó]n") since OCR frequently
# drops diacritics.
_LABEL_PATTERNS: dict[str, list[re.Pattern]] = {
    "folio": [
        re.compile(r"folio\s*(?:no\.?|#|num(?:ero)?\.?)?\s*[:\-]\s*([A-Za-z0-9\-]+)", re.IGNORECASE),
    ],
    "origin": [
        re.compile(r"centro\s+de\s+explotaci[oó]n\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
        re.compile(r"(?:origen|procedencia|punto\s*a)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "secondary_origin": [
        re.compile(r"centro\s+de\s+acopio\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "destination": [
        re.compile(r"destino\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "contract_number": [
        re.compile(r"contrato\s*[:\-]\s*([A-Za-z0-9\-]+)", re.IGNORECASE),
    ],
    "fletero": [
        re.compile(r"datos\s+del\s+chofer(?:\s+del\s+cami[oó]n)?\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
        re.compile(r"(?:fletero|operador|transportista|chofer)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "truck_box_number": [
        re.compile(r"no\.?\s*caja\s*[:\-]\s*([A-Za-z0-9\-]+)", re.IGNORECASE),
    ],
    "material": [
        re.compile(r"(?:material|producto)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
}

_TEXT_FIELDS = (
    "folio",
    "origin",
    "secondary_origin",
    "destination",
    "contract_number",
    "fletero",
    "truck_box_number",
    "material",
)
# Fields required for the exception evaluator / composite score -- keep in
# sync with app/engines/exceptions.py's REQUIRED_FIELDS.
REQUIRED_TEXT_FIELDS = ("folio", "origin", "destination", "fletero")

# Coal quality metrics: captured for provenance, not used in any business
# logic, so a miss is never an exception. Accents optional per the OCR note
# above.
_QUALITY_LABEL_PATTERNS: dict[str, re.Pattern] = {
    "poder_calorifico_superior": re.compile(r"poder\s+calor[ií]fico\s+superior\s*[:\-]?\s*([\d.,]+)", re.IGNORECASE),
    "humedad_pct": re.compile(r"%?\s*humedad\s*[:\-]?\s*([\d.,]+)", re.IGNORECASE),
    "ceniza_pct": re.compile(r"%?\s*ceniza\s*[:\-]?\s*([\d.,]+)", re.IGNORECASE),
    "azufre_pct": re.compile(r"%?\s*azufre\s*[:\-]?\s*([\d.,]+)", re.IGNORECASE),
    "fsi": re.compile(r"\bfsi\s*[:\-]?\s*([\d.,]+)", re.IGNORECASE),
    "granulometria": re.compile(r"granulometr[ií]a\s*[:\-]?\s*([\d.,]+)", re.IGNORECASE),
}

_WEIGHT_DECLARED_PATTERN = re.compile(r"volumen\s+por\s+entregar\s*[:\-]?\s*([^\n\r]+)", re.IGNORECASE)
_WEIGHT_ACTUAL_PATTERN = re.compile(r"volumen\s+entregado\s*[:\-]?\s*([^\n\r]+)", re.IGNORECASE)
# Legacy label, kept as a fallback for any pre-redesign boleta still in circulation.
_WEIGHT_LEGACY_PATTERN = re.compile(r"peso\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE)


@dataclass
class ParsedFields:
    folio: str | None = None
    date: str | None = None
    origin: str | None = None
    secondary_origin: str | None = None
    destination: str | None = None
    contract_number: str | None = None
    material: str | None = None
    fletero: str | None = None
    truck_box_number: str | None = None
    weight: float | None = None  # Volumen Entregado (actual) -- drives tariff/inventory
    weight_declared: float | None = None  # Volumen por Entregar (initial/planned)
    quality_data: dict[str, str] = field(default_factory=dict)
    field_confidences: dict[str, float] = field(default_factory=dict)


def _word_confidence_for_value(value: str, ocr: OCRResult) -> float:
    """Averages the OCR confidence (0-1) of the tokens making up `value`.
    Falls back to the overall OCR confidence if none of the value's tokens
    are individually found among the recognized words (e.g. multi-word
    values whose tokens got merged during OCR)."""
    if not ocr.words:
        return 0.0
    value_tokens = {t.lower() for t in re.findall(r"\w+", value)}
    matched = [
        w.confidence
        for w in ocr.words
        if set(re.findall(r"\w+", w.text.lower())) & value_tokens
    ]
    if matched:
        return round((sum(matched) / len(matched)) / 100.0, 3)
    return round((ocr.confidence / 100.0) * 0.5, 3)  # penalize unverifiable matches


def _extract_quantity(source_text: str) -> float | None:
    """Parses a volume/weight quantity. Tries a unit-suffixed value first
    (kg/toneladas); falls back to a bare number, since the real form
    ("Volumen por Entregar"/"Volumen Entregado") doesn't always print a
    unit inline -- the operation's convention (e.g. toneladas) is assumed
    external to the document. No unit conversion is applied in the fallback
    case; the number is captured as-is."""
    value = parse_weight_kg(source_text)
    if value is not None:
        return value
    match = re.search(r"(\d+[.,]?\d*)", source_text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def parse_fields(ocr: OCRResult) -> ParsedFields:
    text = ocr.text
    parsed = ParsedFields()

    for field_name in _TEXT_FIELDS:
        for pattern in _LABEL_PATTERNS[field_name]:
            match = pattern.search(text)
            if match:
                value = clean_text(match.group(1))
                if value:
                    setattr(parsed, field_name, value)
                    parsed.field_confidences[field_name] = _word_confidence_for_value(value, ocr)
                break
        else:
            parsed.field_confidences[field_name] = 0.0

    parsed.date = parse_date(text)
    parsed.field_confidences["date"] = (
        _word_confidence_for_value(parsed.date, ocr) if parsed.date else 0.0
    )

    weight_match = _WEIGHT_ACTUAL_PATTERN.search(text) or _WEIGHT_LEGACY_PATTERN.search(text)
    weight_source_text = weight_match.group(1) if weight_match else text
    parsed.weight = _extract_quantity(weight_source_text) if weight_match else None
    parsed.field_confidences["weight"] = (
        _word_confidence_for_value(str(parsed.weight), ocr) if parsed.weight is not None else 0.0
    )

    declared_match = _WEIGHT_DECLARED_PATTERN.search(text)
    if declared_match:
        parsed.weight_declared = _extract_quantity(declared_match.group(1))

    for key, pattern in _QUALITY_LABEL_PATTERNS.items():
        match = pattern.search(text)
        if match:
            parsed.quality_data[key] = match.group(1).replace(",", ".")

    return parsed
