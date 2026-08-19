"""Historical swap-in point for LLM/cloud OCR (for handwriting Tesseract reads
poorly). This is now implemented: see `app/ocr/openai_adapter.py`
(`OpenAIOCRAdapter`) and `app/ocr/factory.py` (`get_ocr_adapter`, which wires
it in as an automatic fallback via `OCR_BACKEND`).

`LLMFallbackOCRAdapter` is kept as a backward-compatible alias so older
references/imports keep working.
"""
from __future__ import annotations

from app.ocr.openai_adapter import OpenAIOCRAdapter


class LLMFallbackOCRAdapter(OpenAIOCRAdapter):
    """Deprecated alias for :class:`OpenAIOCRAdapter`."""
