"""Stub swap-in point for a cloud OCR API or LLM-based text extraction
(e.g. for handwritten notes Tesseract reads poorly).

Not implemented in v1 — no external OCR/LLM API credentials are assumed to
be configured. Implement `extract()` here once an API key is available,
then point `app/pipeline/orchestrator.py` at this adapter (or make it a
fallback that re-OCRs low-confidence Tesseract results) without changing
any other module, since both adapters share the OCRAdapter interface.
"""
from __future__ import annotations

from pathlib import Path

from app.ocr.base import OCRAdapter, OCRResult


class LLMFallbackOCRAdapter(OCRAdapter):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    def extract(self, image_path: Path) -> OCRResult:
        raise NotImplementedError(
            "LLMFallbackOCRAdapter is a v1 stub. Implement extract() against your "
            "chosen cloud OCR/LLM API once credentials are available; it must "
            "return an OCRResult exactly like TesseractOCRAdapter does."
        )
