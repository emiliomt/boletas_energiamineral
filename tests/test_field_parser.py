from __future__ import annotations

from app.ocr.base import OCRResult, OCRWord
from app.parsing.field_parser import parse_fields

SAMPLE_TEXT = """\
Folio: B-4521
Fecha: 12/03/2026
Origen: Mina San Jose
Destino: Planta Norte
Material: Carbon
Fletero: Juan Perez
Peso: 8500 kg
"""


def _fake_ocr_result(text: str, confidence: float = 92.0) -> OCRResult:
    words = [
        OCRWord(text=tok, confidence=confidence, left=0, top=0, width=10, height=10)
        for tok in text.split()
    ]
    return OCRResult(text=text, confidence=confidence, words=words)


def test_parses_all_known_fields():
    ocr = _fake_ocr_result(SAMPLE_TEXT)

    parsed = parse_fields(ocr)

    assert parsed.folio == "B-4521"
    assert parsed.date == "2026-03-12"
    assert parsed.origin == "Mina San Jose"
    assert parsed.destination == "Planta Norte"
    assert parsed.material == "Carbon"
    assert parsed.fletero == "Juan Perez"
    assert parsed.weight == 8500.0


def test_high_confidence_tokens_yield_high_field_confidence():
    ocr = _fake_ocr_result(SAMPLE_TEXT, confidence=95.0)

    parsed = parse_fields(ocr)

    assert parsed.field_confidences["folio"] > 0.7
    assert parsed.field_confidences["origin"] > 0.7


def test_missing_field_is_none_with_zero_confidence():
    text = "Folio: B-9001\nFecha: 01/01/2026\n"
    ocr = _fake_ocr_result(text)

    parsed = parse_fields(ocr)

    assert parsed.destination is None
    assert parsed.field_confidences["destination"] == 0.0
    assert parsed.fletero is None
    assert parsed.weight is None


def test_weight_in_tons_converted_to_kg():
    text = "Peso: 8.5 toneladas\n"
    ocr = _fake_ocr_result(text)

    parsed = parse_fields(ocr)

    assert parsed.weight == 8500.0
