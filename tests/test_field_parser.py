from __future__ import annotations

from app.ocr.base import OCRResult, OCRWord
from app.parsing.field_parser import parse_fields

SAMPLE_TEXT = """\
Folio: B-4521
Fecha: 12/03/2026
Destino: C.T. Jose Lopez Portillo
Contrato: 700544405
Datos del chofer del camion: Juan Perez
No. Caja: A-12
Centro de Explotacion: Tajo San Jose
Centro de Acopio: Patio Rosita
Volumen por Entregar: 9000
Volumen Entregado: 8500
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
    assert parsed.origin == "Tajo San Jose"
    assert parsed.secondary_origin == "Patio Rosita"
    assert parsed.destination == "C.T. Jose Lopez Portillo"
    assert parsed.contract_number == "700544405"
    assert parsed.fletero == "Juan Perez"
    assert parsed.truck_box_number == "A-12"
    assert parsed.weight == 8500.0
    assert parsed.weight_declared == 9000.0


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
    assert parsed.weight_declared is None


def test_weight_in_tons_converted_to_kg():
    text = "Volumen Entregado: 8.5 toneladas\n"
    ocr = _fake_ocr_result(text)

    parsed = parse_fields(ocr)

    assert parsed.weight == 8500.0


def test_bare_volumen_number_captured_without_unit():
    # The real form doesn't always print a unit inline -- capture the bare
    # number as-is (no kg/ton conversion) rather than treating it as missing.
    text = "Volumen Entregado: 9000\nVolumen por Entregar: 9200\n"
    ocr = _fake_ocr_result(text)

    parsed = parse_fields(ocr)

    assert parsed.weight == 9000.0
    assert parsed.weight_declared == 9200.0


# OCR of a freshly generated boleta that has NOT been filled in by hand yet:
# every field is blank, and (per app/qr/batch_pdf.py's layout) some labels
# share a line ("Destino:" + "Contrato:") while others are followed by the
# next field's label on the next line. None of these labels are values.
BLANK_GENERATED_BOLETA_TEXT = """\
REPORTE DE CALIDAD Y ORIGEN DEL CARBON
Folio: B-9001
Fecha:
Proveedor: ENERGIA MINERAL, S.A. DE C.V.
Destino: Contrato:
Datos del chofer del camion:
No. Caja:
ORIGEN DEL CARBON
Centro de Explotacion:
Centro de Acopio:
Volumen por Entregar: Volumen Entregado:
"""


def test_blank_generated_boleta_stores_no_bogus_field_values():
    # Regression: scanning a blank generated boleta used to store adjacent
    # field *labels* as values (origin="Centro de Acopio", destination=
    # "Contrato", fletero="No. Caja"). Empty fields must parse as None.
    ocr = _fake_ocr_result(BLANK_GENERATED_BOLETA_TEXT)

    parsed = parse_fields(ocr)

    assert parsed.folio == "B-9001"  # the one real datum, from the QR/label
    assert parsed.origin is None
    assert parsed.secondary_origin is None
    assert parsed.destination is None
    assert parsed.contract_number is None
    assert parsed.fletero is None
    assert parsed.truck_box_number is None
    assert parsed.weight is None
    assert parsed.weight_declared is None


def test_same_line_label_pair_does_not_bleed_into_value():
    # "Destino: Planta Norte Contrato: C-1234" on one line -> destination is
    # just "Planta Norte" (the trailing "Contrato:" label is cut off).
    text = "Destino: Planta Norte Contrato: C-1234\n"
    ocr = _fake_ocr_result(text)

    parsed = parse_fields(ocr)

    assert parsed.destination == "Planta Norte"
    assert parsed.contract_number == "C-1234"


def test_quality_metrics_captured_best_effort():
    text = "Poder Calorifico Superior: 5200\n% Humedad: 4.3\nFSI: 6.5\n"
    ocr = _fake_ocr_result(text)

    parsed = parse_fields(ocr)

    assert parsed.quality_data["poder_calorifico_superior"] == "5200"
    assert parsed.quality_data["humedad_pct"] == "4.3"
    assert parsed.quality_data["fsi"] == "6.5"
