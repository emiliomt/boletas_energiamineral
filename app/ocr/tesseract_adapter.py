"""Default OCR backend: local Tesseract via pytesseract. Offline, free, no
API key required — the right default for a first-pass tool with unknown
external-credential availability. Swap in a cloud/LLM adapter later by
implementing OCRAdapter and pointing the pipeline at it (see
llm_fallback_adapter.py for the interface shape)."""
from __future__ import annotations

from pathlib import Path

import pytesseract
from PIL import Image

from app.config import settings
from app.ocr.base import OCRAdapter, OCRResult, OCRWord


class TesseractOCRAdapter(OCRAdapter):
    def __init__(self, language: str | None = None):
        self.language = language or settings.ocr_language

    def extract(self, image_path: Path) -> OCRResult:
        image = Image.open(image_path)

        text = pytesseract.image_to_string(image, lang=self.language)

        data = pytesseract.image_to_data(
            image, lang=self.language, output_type=pytesseract.Output.DICT
        )

        words: list[OCRWord] = []
        confidences: list[float] = []
        for i, raw_conf in enumerate(data["conf"]):
            token = data["text"][i].strip()
            try:
                conf = float(raw_conf)
            except (TypeError, ValueError):
                conf = -1.0
            if not token or conf < 0:
                continue
            words.append(
                OCRWord(
                    text=token,
                    confidence=conf,
                    left=data["left"][i],
                    top=data["top"][i],
                    width=data["width"][i],
                    height=data["height"][i],
                )
            )
            confidences.append(conf)

        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OCRResult(text=text, confidence=overall_confidence, words=words)
