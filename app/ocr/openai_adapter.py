"""OpenAI vision-model OCR backend.

Unlike Tesseract (printed-text only), a vision LLM reads the *handwritten*
fields on a photographed boleta (fecha, chofer, no. caja, volúmenes) far more
reliably. To stay a drop-in `OCRAdapter`, this doesn't invent its own field
schema: it transcribes the boleta into the exact "Label: value" line format
the rules-based parser already understands (app/parsing/field_parser.py), so
every downstream stage (parsing, confidence scoring, exceptions) is unchanged.
The model does the hard reading; the deterministic rules still do extraction.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from app.config import settings
from app.ocr.base import OCRAdapter, OCRResult, OCRWord

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# Labels must match app/parsing/field_parser.py so parse_fields() can read the
# transcription back out. Blank fields are omitted rather than guessed.
_SYSTEM_PROMPT = (
    "You transcribe scanned/photographed Mexican coal-delivery boletas "
    '("REPORTE DE CALIDAD Y ORIGEN DEL CARBON"). Read ALL text including '
    "handwriting. Return ONLY JSON: "
    '{"transcription": "<lines>", "confidence": <0-100 integer>}. '
    "In `transcription`, output one 'Label: value' line per field that has a "
    "value (printed or handwritten), copying the value verbatim. Omit fields "
    "that are blank. Use EXACTLY these labels when present:\n"
    "Folio, Fecha, Proveedor, Destino, Contrato, "
    "Datos del chofer del camion, No. Caja, "
    "Poder Calorifico Superior, % Humedad, % Ceniza, % Azufre, FSI, "
    "Granulometria, Centro de Explotacion, Centro de Acopio, "
    "Datos de Concesion Minera, Volumen por Entregar, Volumen Entregado, "
    "Nombre.\n"
    "`confidence` is your overall transcription confidence."
)


class OpenAIError(RuntimeError):
    """Raised when the OpenAI backend can't produce a result (no key, network
    failure, bad response). The composite adapter catches this to fall back."""


class OpenAIOCRAdapter(OCRAdapter):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_ocr_model

    def _data_url(self, image_path: Path) -> str:
        mime = _MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/png")
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def extract(self, image_path: Path) -> OCRResult:
        if not self.api_key:
            raise OpenAIError("OPENAI_API_KEY is not configured")

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise OpenAIError("openai package is not installed") from exc

        client = OpenAI(api_key=self.api_key)
        try:
            response = client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Transcribe this boleta."},
                            {"type": "image_url", "image_url": {"url": self._data_url(image_path)}},
                        ],
                    },
                ],
            )
        except Exception as exc:  # network/auth/rate-limit/etc.
            raise OpenAIError(f"OpenAI request failed: {exc}") from exc

        content = (response.choices[0].message.content or "").strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIError(f"OpenAI returned non-JSON content: {content[:200]!r}") from exc

        text = str(payload.get("transcription", "")).strip()
        try:
            confidence = float(payload.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(100.0, confidence))

        # Synthesize per-word boxes so field-confidence scoring has something to
        # average; positions are unknown from the LLM, so they're left at 0.
        words = [
            OCRWord(text=tok, confidence=confidence, left=0, top=0, width=0, height=0)
            for tok in text.split()
        ]
        return OCRResult(text=text, confidence=confidence, words=words, engine=f"openai:{self.model}")
