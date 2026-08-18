from __future__ import annotations

import shutil

import pytest
from PIL import Image, ImageDraw

from app.ocr.tesseract_adapter import TesseractOCRAdapter

requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)


def _make_text_image(tmp_path, text: str):
    img = Image.new("RGB", (600, 120), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 40), text, fill="black")
    path = tmp_path / "sample.png"
    img.save(path)
    return path


@requires_tesseract
def test_extract_returns_text_and_confidence(tmp_path):
    image_path = _make_text_image(tmp_path, "Folio: A123")
    adapter = TesseractOCRAdapter(language="eng")

    result = adapter.extract(image_path)

    assert "Folio" in result.text or "A123" in result.text
    assert 0.0 <= result.confidence <= 100.0
    assert isinstance(result.words, list)


@requires_tesseract
def test_extract_word_confidences_are_bounded(tmp_path):
    image_path = _make_text_image(tmp_path, "Origen: Mina San Jose")
    adapter = TesseractOCRAdapter(language="eng")

    result = adapter.extract(image_path)

    for word in result.words:
        assert 0.0 <= word.confidence <= 100.0
