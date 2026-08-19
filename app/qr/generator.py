"""QR image generation for pre-issued folios.

The QR encodes only `qr_payload` (e.g. "BOL:B-3201") — no signing/expiry.
The security boundary is the `Folio` table lookup at scan time (see
app/engines/folio_registry.py), which is appropriate for an internal
ops/fraud-reduction check, not adversarial security.
"""
from __future__ import annotations

import qrcode
from PIL.Image import Image

QR_PAYLOAD_PREFIX = "BOL:"


def qr_payload_for_folio(folio: str) -> str:
    return f"{QR_PAYLOAD_PREFIX}{folio}"


def generate_qr_image(payload: str) -> Image:
    """Renders `payload` as a QR code, returning a real PIL Image (not
    qrcode's own PilImage wrapper -- callers like reportlab's ImageReader
    and pyzbar's decode() expect an actual PIL.Image.Image)."""
    return qrcode.make(payload).get_image()
