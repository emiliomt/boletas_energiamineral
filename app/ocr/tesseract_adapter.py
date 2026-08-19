"""Default OCR backend: local Tesseract via pytesseract. Offline, free, no
API key required — the right default for a first-pass tool with unknown
external-credential availability. Swap in a cloud/LLM adapter later by
implementing OCRAdapter and pointing the pipeline at it (see
llm_fallback_adapter.py for the interface shape)."""
from __future__ import annotations

from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

from app.config import settings
from app.ocr.base import OCRAdapter, OCRResult, OCRWord

# Tesseract does best on a flat, high-contrast, reasonably large grayscale
# image. Real uploads are often phone photos (angled, low-contrast, small),
# so a little cheap preprocessing meaningfully lifts recognition. Target long
# edge for upscaling small images (Tesseract likes ~30px cap-height text).
_MIN_LONG_EDGE = 2000


def _preprocess(image: Image.Image) -> Image.Image:
    """Cheap, safe preprocessing before OCR: honor EXIF rotation (phone
    photos), flatten to grayscale, upscale small images, and stretch contrast.
    Deliberately conservative -- no binarization/deskew that could distort a
    clean scan; those (and handwriting) are better handled by a cloud/LLM OCR
    backend (see app/ocr/llm_fallback_adapter.py)."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")

    long_edge = max(image.size)
    if long_edge < _MIN_LONG_EDGE:
        scale = _MIN_LONG_EDGE / long_edge
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.LANCZOS)

    return ImageOps.autocontrast(image)


class TesseractOCRAdapter(OCRAdapter):
    def __init__(self, language: str | None = None):
        self.language = language or settings.ocr_language

    def extract(self, image_path: Path) -> OCRResult:
        image = _preprocess(Image.open(image_path))

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
        return OCRResult(text=text, confidence=overall_confidence, words=words, engine="tesseract")
