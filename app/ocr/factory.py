"""Selects the OCR backend from settings, so ingestion code depends on a
single `get_ocr_adapter()` call instead of hard-wiring Tesseract.

`auto` mode is the safe default: it uses Tesseract, and only escalates a
low-confidence result to OpenAI when a key is actually configured, falling
back to the Tesseract result if the OpenAI call errors. With no key set,
`auto` behaves exactly like plain Tesseract.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.ocr.base import OCRAdapter, OCRResult
from app.ocr.openai_adapter import OpenAIError, OpenAIOCRAdapter
from app.ocr.tesseract_adapter import TesseractOCRAdapter

logger = logging.getLogger(__name__)


class FallbackOCRAdapter(OCRAdapter):
    """Runs `primary`; if its confidence is below `min_confidence`, re-OCRs
    with `fallback`. Any fallback error is swallowed and the primary result is
    returned, so enabling the fallback can never make a scan fail."""

    def __init__(self, primary: OCRAdapter, fallback: OCRAdapter, min_confidence: float):
        self.primary = primary
        self.fallback = fallback
        self.min_confidence = min_confidence

    def extract(self, image_path: Path) -> OCRResult:
        primary_result = self.primary.extract(image_path)
        if primary_result.confidence >= self.min_confidence:
            return primary_result

        try:
            fallback_result = self.fallback.extract(image_path)
        except OpenAIError as exc:
            logger.warning("OCR fallback unavailable, keeping primary result: %s", exc)
            return primary_result

        # Keep whichever transcription looks more complete; the fallback exists
        # precisely because the primary was low-confidence, so prefer it when it
        # actually returned text.
        if fallback_result.text.strip():
            return fallback_result
        return primary_result


def get_ocr_adapter() -> OCRAdapter:
    backend = (settings.ocr_backend or "tesseract").lower()
    has_key = bool(settings.openai_api_key)

    if backend == "tesseract":
        return TesseractOCRAdapter()

    if backend == "openai":
        if has_key:
            return OpenAIOCRAdapter()
        logger.warning("OCR_BACKEND=openai but OPENAI_API_KEY is not set; using Tesseract.")
        return TesseractOCRAdapter()

    # "auto" (default) and any unknown value.
    if has_key:
        return FallbackOCRAdapter(
            primary=TesseractOCRAdapter(),
            fallback=OpenAIOCRAdapter(),
            min_confidence=settings.ocr_fallback_min_confidence,
        )
    return TesseractOCRAdapter()
