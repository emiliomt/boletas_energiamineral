"""OCR/pipeline work that must not run inside the Twilio webhook (15s cap)."""
from __future__ import annotations

import logging

from app.db import SessionLocal
from app.models import Boleta
from app.ocr.factory import get_ocr_adapter
from app.pipeline.orchestrator import process_boleta

logger = logging.getLogger(__name__)


def process_boleta_ids(boleta_ids: list[int]) -> None:
    """Open a fresh DB session and run the normal pipeline per boleta."""
    if not boleta_ids:
        return
    db = SessionLocal()
    try:
        adapter = get_ocr_adapter()
        for boleta_id in boleta_ids:
            boleta = db.get(Boleta, boleta_id)
            if boleta is None:
                continue
            process_boleta(db, boleta, adapter)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("WhatsApp background OCR failed for boletas %s", boleta_ids)
    finally:
        db.close()
