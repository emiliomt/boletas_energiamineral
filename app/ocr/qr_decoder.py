"""Decodes a folio out of a QR code printed on a scanned boleta.

Lives alongside tesseract_adapter.py (not a separate top-level package)
since it's the same kind of job: read something off the raw scanned image.
Returns a plain folio string rather than an OCRResult -- it isn't an
OCRAdapter, just a small standalone step the pipeline runs before OCR.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps
from pyzbar.pyzbar import decode as zbar_decode

from app.qr.generator import QR_PAYLOAD_PREFIX


def decode_qr_folio(image_path: Path, prefix: str = QR_PAYLOAD_PREFIX) -> str | None:
    """Returns the folio if a QR with the expected prefix decodes from the
    image, else None. Tries a plain decode first, then one cheap fallback
    (autocontrast) for a slightly low-contrast photo -- not a full
    deskew/rotation pipeline; that's a future enhancement if real-world
    scans prove it's needed, same as the OCR adapter's own swap-in point."""
    try:
        image = Image.open(image_path)
    except (OSError, FileNotFoundError):
        # Missing/corrupt/unreadable file -- treat as "no QR" rather than
        # crashing the pipeline; OCR (or its own failure) still runs.
        return None

    for candidate_image in (image, ImageOps.autocontrast(image.convert("L"))):
        for result in zbar_decode(candidate_image):
            payload = result.data.decode("utf-8", errors="ignore")
            if payload.startswith(prefix):
                return payload[len(prefix):]

    return None
