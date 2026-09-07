from __future__ import annotations

from pathlib import Path

from app.ocr.base import OCRResult
from app.parsing.field_parser import parse_fields
from app.parsing.normalizers import parse_date


def _run_parse(text_path: Path):
    text = Path(text_path).read_text()
    ocr = OCRResult(text=text, confidence=85.0, words=[], engine="openai:vision-manual")
    return parse_fields(ocr)


def test_parse_date_two_digit_year_mapping():
    assert parse_date("Fecha: 13/07/26") == "2026-07-13"
    assert parse_date("Fecha: 31-12-69") == "2069-12-31"
    assert parse_date("Fecha: 01/01/00") == "2000-01-01"
    assert parse_date("Fecha: 13-07-96") == "1996-07-13"
    # Existing formats still work
    assert parse_date("Fecha: 2026-07-13") == "2026-07-13"
    assert parse_date("Fecha: 13/07/2026") == "2026-07-13"


def test_truck_box_number_allows_multi_token():
    text = "No. CAJA: Roc 1274\n"
    parsed = _run_parse(Path(__file__).parent / "fixtures" / "real_vision" / "boleta_a_vision.txt")
    assert parsed.truck_box_number in ("Roc 1274", "Reg 1274")  # specific file asserts below

    parsed_inline = _run_parse(
        Path(__file__).parent / "fixtures" / "real_vision" / "boleta_a_vision.txt"
    )
    assert parsed_inline.truck_box_number == "Roc 1274"


def test_folio_fallback_prefers_six_digits_over_five():
    a = _run_parse(Path(__file__).parent / "fixtures" / "real_vision" / "boleta_a_vision.txt")
    b = _run_parse(Path(__file__).parent / "fixtures" / "real_vision" / "boleta_b_vision.txt")
    assert a.folio == "003612"
    assert b.folio == "003752"


def test_core_fields_from_real_vision_transcripts():
    a = _run_parse(Path(__file__).parent / "fixtures" / "real_vision" / "boleta_a_vision.txt")
    b = _run_parse(Path(__file__).parent / "fixtures" / "real_vision" / "boleta_b_vision.txt")
    # Dates (two-digit year) recognized
    assert a.date == "2026-07-13"
    assert b.date == "2026-07-17"
    # Destino / Origen / Acopio / Chofer
    for p in (a, b):
        assert p.destination == "C.T. JOSE LOPEZ PORTILLO"
        assert p.origin == "TAJO SAN JOSE"
        assert p.secondary_origin == "PATIO ROSITA"
        assert p.fletero == "Isidro Sanchez"
    # No. Caja multi-token correct per file
    assert a.truck_box_number == "Roc 1274"
    assert b.truck_box_number == "Reg 1274"
