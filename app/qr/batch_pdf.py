"""Generates the print-ready PDF handed to the print vendor for a
FolioBatch: one full boleta page per folio, replacing the client's
previous pre-printed booklet design with our own template that adds a QR
code next to the folio number.

Field layout mirrors the client's real "Reporte de Calidad y Origen del
Carbón" form (Proveedor / Destino+Contrato / Datos del chofer / No. Caja /
coal quality metrics / Centro de Explotación+Acopio / Concesión Minera /
Volumen por Entregar + Volumen Entregado / Representante Legal). The
folio+QR replace the vendor's own pre-printed serial. Field labels here
must stay in sync with app/parsing/field_parser.py's regexes, since this
template *is* what later gets OCR'd back out at Point B.

Uses reportlab (vector PDF -- crisp text/lines at real print resolution,
unlike a rasterized-PNG-to-PDF approach) rather than the PIL-based pattern
in scripts/generate_sample_fixtures.py, which exists to synthesize *test
scans*, a different job from producing a *press-ready print file*.
"""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.models import Folio, FolioBatch
from app.qr.generator import generate_qr_image

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 15 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
QR_SIZE = 26 * mm

# Fixed per the client's current operation; not per-boleta data.
PROVEEDOR_NAME = "ENERGIA MINERAL, S.A. DE C.V."

QUALITY_FIELD_LABELS = [
    "Poder Calorifico Superior:",
    "% Humedad:",
    "% Ceniza:",
    "% Azufre:",
    "FSI:",
    "Granulometria:",
]


def _label_line(c: canvas.Canvas, x: float, y: float, label: str, line_from_x: float, line_to_x: float) -> None:
    c.setFont("Helvetica", 10)
    c.drawString(x, y, label)
    c.line(line_from_x, y - 0.7 * mm, line_to_x, y - 0.7 * mm)


def _section_header(c: canvas.Canvas, x: float, y: float, text: str) -> None:
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, text)


def _draw_boleta_page(c: canvas.Canvas, folio_row: Folio) -> None:
    x_left = MARGIN
    x_right = PAGE_WIDTH - MARGIN
    y = PAGE_HEIGHT - MARGIN

    # Title.
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(PAGE_WIDTH / 2, y, "REPORTE DE CALIDAD Y ORIGEN DEL CARBON")
    y -= 10 * mm

    # QR + folio, top-right.
    qr_image = generate_qr_image(folio_row.qr_payload)
    qr_x = x_right - QR_SIZE
    qr_y = y - QR_SIZE + 6 * mm
    c.drawImage(ImageReader(qr_image), qr_x, qr_y, width=QR_SIZE, height=QR_SIZE)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x_left, y, f"Folio: {folio_row.folio}")
    y -= 10 * mm

    _label_line(c, x_left, y, "Fecha:", x_left + 20 * mm, x_left + 90 * mm)
    y -= 8 * mm

    c.setFont("Helvetica", 10)
    c.drawString(x_left, y, f"Proveedor: {PROVEEDOR_NAME}")
    y -= 8 * mm

    _label_line(c, x_left, y, "Destino:", x_left + 20 * mm, x_left + 110 * mm)
    _label_line(c, x_left + 115 * mm, y, "Contrato:", x_left + 135 * mm, x_right)
    y -= 8 * mm

    _label_line(c, x_left, y, "Datos del chofer del camion:", x_left + 55 * mm, x_right)
    y -= 8 * mm

    _label_line(c, x_left, y, "No. Caja:", x_left + 22 * mm, x_left + 90 * mm)
    y -= 12 * mm

    _section_header(c, x_left, y, "DATOS DE LA CALIDAD DEL CARBON A SUMINISTRAR")
    y -= 8 * mm
    for label in QUALITY_FIELD_LABELS:
        _label_line(c, x_left + 4 * mm, y, label, x_left + 55 * mm, x_left + 100 * mm)
        y -= 7 * mm
    y -= 4 * mm

    _section_header(c, x_left, y, "ORIGEN DEL CARBON")
    y -= 8 * mm
    _label_line(c, x_left + 4 * mm, y, "Centro de Explotacion:", x_left + 50 * mm, x_right)
    y -= 7 * mm
    _label_line(c, x_left + 4 * mm, y, "Centro de Acopio:", x_left + 50 * mm, x_right)
    y -= 7 * mm
    _label_line(c, x_left + 4 * mm, y, "Datos de Concesion Minera:", x_left + 60 * mm, x_right)
    y -= 12 * mm

    _label_line(c, x_left, y, "Volumen por Entregar:", x_left + 45 * mm, x_left + 100 * mm)
    _label_line(c, x_left + 105 * mm, y, "Volumen Entregado:", x_left + 145 * mm, x_right)
    y -= 14 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        x_left, y,
        "Declaro que todos los datos asentados en el presente informe son ciertos y pueden ser verificados en cualquier momento.",
    )
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_left, y, "REPRESENTANTE LEGAL")
    y -= 8 * mm
    _label_line(c, x_left, y, "Nombre:", x_left + 20 * mm, x_right)
    y -= 8 * mm
    _label_line(c, x_left, y, "Firma:", x_left + 18 * mm, x_right)


def generate_batch_pdf(folio_batch: FolioBatch, folios: list[Folio]) -> bytes:
    """Pure function: batch + its folios in, print-ready PDF bytes out --
    one full boleta page per folio."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setTitle(f"Boletas - {folio_batch.label}")

    for i, folio_row in enumerate(folios):
        if i > 0:
            c.showPage()
        _draw_boleta_page(c, folio_row)

    c.save()
    return buffer.getvalue()
