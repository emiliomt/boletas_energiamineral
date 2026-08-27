"""Public Twilio WhatsApp webhook. Signature-validated, not admin-gated."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

from app.config import settings
from app import db as app_db
from app.whatsapp.background import process_boleta_ids
from app.whatsapp.handler import handle_inbound
from app.whatsapp.numbers import normalize_sender, sender_is_allowed
from app.whatsapp.security import signature_is_valid

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

TWILIO_WEBHOOK_PATH = "/webhooks/twilio/whatsapp"


def _twiml(text: str) -> Response:
    response = MessagingResponse()
    response.message(text)
    return Response(content=str(response), media_type="application/xml")


@router.post(TWILIO_WEBHOOK_PATH)
async def twilio_whatsapp(request: Request, background_tasks: BackgroundTasks) -> Response:
    if not (settings.twilio_auth_token and settings.twilio_account_sid):
        return Response("Twilio not configured", status_code=503)

    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature_is_valid(request, params, signature):
        logger.warning("Rejected Twilio WhatsApp webhook with invalid signature")
        return Response("Forbidden", status_code=403)

    sender = normalize_sender(params.get("From") or "")
    if not sender_is_allowed(sender):
        logger.warning("Rejected WhatsApp sender %s (not on allowlist)", sender)
        return _twiml("Este número no está autorizado para subir boletas.")

    db = app_db.SessionLocal()
    try:
        result = handle_inbound(db, params)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("WhatsApp inbound handler failed")
        return _twiml("Hubo un error al guardar. Mandá la foto de nuevo.")
    finally:
        db.close()

    if result.boleta_ids:
        background_tasks.add_task(process_boleta_ids, result.boleta_ids)
    return _twiml(result.reply)
