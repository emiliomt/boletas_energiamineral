"""End-to-end: a real boleta image carrying an embedded QR (not just OCR'd
text) runs through the real pipeline. Confirms the QR wins over OCR for
the folio, the folio registry gets linked, and a duplicate scan of the
same QR is caught."""
from __future__ import annotations

import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.models import Batch, Boleta, Folio, FolioBatch
from app.ocr.tesseract_adapter import TesseractOCRAdapter
from app.pipeline.orchestrator import process_boleta
from app.qr.generator import generate_qr_image, qr_payload_for_folio

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not available")


def _font(size: int = 20):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _render_boleta_with_qr(path, folio: str, extra_lines: list[str]) -> None:
    """Mirrors the real template: QR top-right + hand-filled field text,
    but simplified (single-column) since only the parsing behavior matters
    here, not print layout (that's covered by inspecting batch_pdf.py's
    output directly)."""
    canvas = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font()

    qr_image = generate_qr_image(qr_payload_for_folio(folio)).convert("RGB").resize((120, 120))
    canvas.paste(qr_image, (740, 20))

    y = 30
    for line in extra_lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 40

    canvas.save(path)


def _seed_folio(db_session, folio: str) -> FolioBatch:
    batch = FolioBatch(label=f"seed-{folio}", mode="imported", count=1)
    db_session.add(batch)
    db_session.flush()
    db_session.add(Folio(folio_batch_id=batch.id, folio=folio, qr_payload=qr_payload_for_folio(folio)))
    db_session.flush()
    return batch


def _make_boleta(db_session, filename: str) -> Boleta:
    batch = Batch(label="qr-integration-test")
    db_session.add(batch)
    db_session.flush()
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=str(filename),
        stored_path=str(filename),
        mime_type="image/png",
        page_number=1,
        sha256_hash="n/a",
    )
    db_session.add(boleta)
    db_session.flush()
    return boleta


def test_qr_folio_wins_and_gets_linked(db_session, tmp_path):
    _seed_folio(db_session, "Q-5001")
    image_path = tmp_path / "boleta_with_qr.png"
    _render_boleta_with_qr(
        image_path,
        "Q-5001",
        [
            "Fecha: 15/01/2026",
            "Centro de Explotacion: Mina San Jose",
            "Destino: Planta Norte",
            "Datos del chofer del camion: Juan Perez",
            "Volumen Entregado: 9000 kg",
        ],
    )
    boleta = _make_boleta(db_session, image_path)

    record = process_boleta(db_session, boleta, TesseractOCRAdapter())

    assert record.folio == "Q-5001"
    assert record.field_confidences["folio"] == 1.0
    assert "unknown_folio" not in record.exceptions
    assert record.status == "auto_processed"

    row = db_session.query(Folio).filter_by(folio="Q-5001").one()
    assert row.status == "scanned"
    assert row.boleta_record_id == record.id


def test_reprocessing_same_boleta_stays_ok(db_session, tmp_path):
    _seed_folio(db_session, "Q-5002")
    image_path = tmp_path / "boleta_reprocess.png"
    _render_boleta_with_qr(
        image_path,
        "Q-5002",
        [
            "Fecha: 15/01/2026",
            "Centro de Explotacion: Mina San Jose",
            "Destino: Planta Norte",
            "Datos del chofer del camion: Juan Perez",
            "Volumen Entregado: 9000 kg",
        ],
    )
    boleta = _make_boleta(db_session, image_path)

    first = process_boleta(db_session, boleta, TesseractOCRAdapter())
    second = process_boleta(db_session, boleta, TesseractOCRAdapter())

    assert first.id == second.id
    assert "folio_already_used" not in second.exceptions


def test_second_boleta_with_same_qr_is_flagged_already_used(db_session, tmp_path):
    _seed_folio(db_session, "Q-5003")
    lines = [
        "Fecha: 15/01/2026",
        "Centro de Explotacion: Mina San Jose",
        "Destino: Planta Norte",
        "Datos del chofer del camion: Juan Perez",
        "Volumen Entregado: 9000 kg",
    ]
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    _render_boleta_with_qr(first_path, "Q-5003", lines)
    _render_boleta_with_qr(second_path, "Q-5003", lines)

    first_boleta = _make_boleta(db_session, first_path)
    process_boleta(db_session, first_boleta, TesseractOCRAdapter())

    second_boleta = _make_boleta(db_session, second_path)
    second_record = process_boleta(db_session, second_boleta, TesseractOCRAdapter())

    assert "folio_already_used" in second_record.exceptions
    assert second_record.status == "needs_review"


def test_unseeded_qr_folio_flags_unknown(db_session, tmp_path):
    image_path = tmp_path / "boleta_unknown.png"
    _render_boleta_with_qr(
        image_path,
        "Q-9999",
        ["Fecha: 15/01/2026", "Centro de Explotacion: Mina San Jose", "Destino: Planta Norte"],
    )
    boleta = _make_boleta(db_session, image_path)

    record = process_boleta(db_session, boleta, TesseractOCRAdapter())

    assert "unknown_folio" in record.exceptions
    assert record.status == "needs_review"
