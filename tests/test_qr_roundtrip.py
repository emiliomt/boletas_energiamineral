from __future__ import annotations

import pytest
from PIL import Image

from app.ocr.qr_decoder import decode_qr_folio
from app.qr.generator import generate_qr_image, qr_payload_for_folio

# pyzbar links libzbar at import time rather than shelling out to a binary,
# so there's no CLI tool to probe for; skip only if the import itself fails.
try:
    import pyzbar.pyzbar  # noqa: F401

    _ZBAR_AVAILABLE = True
except (ImportError, OSError):
    _ZBAR_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _ZBAR_AVAILABLE, reason="libzbar not available")


def test_generate_then_decode_round_trip(tmp_path):
    folio = "B-3201"
    payload = qr_payload_for_folio(folio)

    qr_image = generate_qr_image(payload)
    # Paste onto a plain canvas, like the QR would sit within a printed page.
    canvas = Image.new("RGB", (400, 400), "white")
    canvas.paste(qr_image.convert("RGB"), (50, 50))
    path = tmp_path / "qr.png"
    canvas.save(path)

    result = decode_qr_folio(path)

    assert result == folio


def test_decode_returns_none_for_image_without_qr(tmp_path):
    canvas = Image.new("RGB", (200, 200), "white")
    path = tmp_path / "blank.png"
    canvas.save(path)

    assert decode_qr_folio(path) is None


def test_decode_returns_none_for_missing_file(tmp_path):
    assert decode_qr_folio(tmp_path / "does_not_exist.png") is None


def test_decode_ignores_qr_with_wrong_prefix(tmp_path):
    qr_image = generate_qr_image("OTHER:not-a-folio")
    canvas = Image.new("RGB", (400, 400), "white")
    canvas.paste(qr_image.convert("RGB"), (50, 50))
    path = tmp_path / "wrong_prefix.png"
    canvas.save(path)

    assert decode_qr_folio(path) is None
