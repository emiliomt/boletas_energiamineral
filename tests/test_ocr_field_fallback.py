from __future__ import annotations

from pathlib import Path

from app.ocr.base import OCRResult
from app.ocr.factory import FallbackOCRAdapter
from app.ocr.tesseract_adapter import TesseractOCRAdapter


class _TessStub(TesseractOCRAdapter):
    """Tesseract-like stub that returns a fixed OCRResult without calling tesseract."""

    def __init__(self, text: str, confidence: float):
        super().__init__(language="eng")
        self._text = text
        self._confidence = confidence

    def extract(self, image_path: Path) -> OCRResult:
        return OCRResult(text=self._text, confidence=self._confidence, words=[], engine="tesseract")


class _FallbackStub:
    def __init__(self, text: str, confidence: float, engine: str = "openai:gpt-4o-mini"):
        self._result = OCRResult(text=text, confidence=confidence, words=[], engine=engine)

    def extract(self, image_path: Path) -> OCRResult:
        return self._result


def _img(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    return p


def test_field_aware_fallback_escalates_on_missing_core_fields(tmp_path, monkeypatch):
    # Primary has high overall confidence but misses multiple core fields (common with handwriting).
    primary_text = (
        "Folio:\n"
        "Centro de Explotacion:\n"
        "Destino:\n"
        "Datos del chofer del camion:\n"
    )
    fallback_text = (
        "Folio: B-123\n"
        "Centro de Explotacion: Mina San Jose\n"
        "Destino: Planta Norte\n"
        "Datos del chofer del camion: Juan Perez\n"
    )
    adapter = FallbackOCRAdapter(
        primary=_TessStub(primary_text, 92.0),
        fallback=_FallbackStub(fallback_text, 88.0),
        min_confidence=70.0,
    )

    result = adapter.extract(_img(tmp_path))

    # Despite high primary confidence, the adapter should prefer the fallback which filled fields.
    assert "Juan Perez" in result.text
    assert "Planta Norte" in result.text
    assert result.engine.startswith("openai:")


def test_field_aware_fallback_keeps_primary_when_fields_present(tmp_path):
    primary_text = (
        "Folio: B-321\n"
        "Centro de Explotacion: Mina San Jose\n"
        "Destino: Planta Norte\n"
        "Datos del chofer del camion: Maria Lopez\n"
    )
    adapter = FallbackOCRAdapter(
        primary=_TessStub(primary_text, 92.0),
        fallback=_FallbackStub("irrelevant", 50.0),
        min_confidence=70.0,
    )

    result = adapter.extract(_img(tmp_path))

    assert "Maria Lopez" in result.text
    assert result.engine == "tesseract"
