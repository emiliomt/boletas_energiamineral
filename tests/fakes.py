"""Test-only OCR adapter that returns canned text/confidence instead of
running Tesseract, so pipeline tests are deterministic and don't depend on
OCR accuracy."""
from __future__ import annotations

from pathlib import Path

from app.ocr.base import OCRAdapter, OCRResult, OCRWord


class FakeOCRAdapter(OCRAdapter):
    def __init__(self, text: str, confidence: float):
        self._text = text
        self._confidence = confidence

    def extract(self, image_path: Path) -> OCRResult:
        words = [
            OCRWord(text=tok, confidence=self._confidence, left=0, top=0, width=10, height=10)
            for tok in self._text.split()
        ]
        return OCRResult(text=self._text, confidence=self._confidence, words=words)
