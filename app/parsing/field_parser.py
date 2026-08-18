"""Rule-based field extraction from raw OCR text.

Boletas are assumed to mostly follow a small number of common label formats
(see PDR assumptions). This parser looks for known Spanish field labels
first ("Folio:", "Origen:", ...) with a couple of English/synonym fallbacks,
and computes a per-field confidence from the OCR word-level confidences of
the tokens that make up the matched value. A field that isn't found at all
gets confidence 0 and is left None so the exception evaluator can flag it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ocr.base import OCRResult
from app.parsing.normalizers import clean_text, parse_date, parse_weight_kg

# Each field: ordered list of label regexes tried in turn; first match wins.
_LABEL_PATTERNS: dict[str, list[re.Pattern]] = {
    "folio": [
        re.compile(r"(?:folio|boleta)\s*(?:no\.?|#|num(?:ero)?\.?)?\s*[:\-]\s*([A-Za-z0-9\-]+)", re.IGNORECASE),
    ],
    "origin": [
        re.compile(r"(?:origen|procedencia|punto\s*a)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "destination": [
        re.compile(r"(?:destino|punto\s*b)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "fletero": [
        re.compile(r"(?:fletero|operador|transportista|chofer)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
    "material": [
        re.compile(r"(?:material|producto)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
    ],
}

_TEXT_FIELDS = ("folio", "origin", "destination", "fletero", "material")


@dataclass
class ParsedFields:
    folio: str | None = None
    date: str | None = None
    origin: str | None = None
    destination: str | None = None
    material: str | None = None
    fletero: str | None = None
    weight: float | None = None
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

    weight_match = re.search(r"(?:peso)\s*[:\-]\s*([^\n\r]+)", text, re.IGNORECASE)
    weight_source_text = weight_match.group(1) if weight_match else text
    parsed.weight = parse_weight_kg(weight_source_text)
    parsed.field_confidences["weight"] = (
        _word_confidence_for_value(str(parsed.weight), ocr) if parsed.weight is not None else 0.0
    )

    return parsed
