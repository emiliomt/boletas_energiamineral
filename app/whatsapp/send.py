"""Outbound WhatsApp interactive messages via Twilio REST.

These helpers are best-effort: they no-op when credentials are missing or
when Twilio rejects the shape. The webhook always returns a plain-text
TwiML fallback so the flow remains usable without interactivity.
"""
from __future__ import annotations

import logging
from typing import Iterable, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


def _can_send() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_whatsapp_from)


def _client():
    from twilio.rest import Client  # lazy import to keep tests light
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def send_interactive_lote_list(to_whatsapp: str, rows: list[dict]) -> bool:
    """Send a WhatsApp interactive list with FolioBatch options.

    rows: [{'id': 'lote:123', 'title': 'Label', 'description': '...'}, ...]
    """
    if not _can_send():
        return False
    try:
        interactive = {
            "type": "list",
            "body": {"text": "Elegí el lote de folios usado en esta boleta."},
            "action": {
                "button": "Ver lotes",
                "sections": [
                    {
                        "title": "Lotes de folios",
                        "rows": rows[:10],  # WhatsApp allows up to 10 rows per list
                    }
                ],
            },
        }
        _client().messages.create(
            from_=settings.twilio_whatsapp_from,
            to=to_whatsapp,
            interactive=interactive,
        )
        return True
    except Exception:  # pragma: no cover (network errors)
        logger.exception("Failed to send interactive lote list to %s", to_whatsapp)
        return False


def send_quick_replies(to_whatsapp: str, prompt: str, buttons: Iterable[Tuple[str, str]]) -> bool:
    """Send WhatsApp quick-reply buttons.

    buttons: iterable of (payload_id, title)
    """
    if not _can_send():
        return False
    try:
        interactive = {
            "type": "button",
            "body": {"text": prompt},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": payload, "title": title}} for payload, title in list(buttons)[:3]
                ]
            },
        }
        _client().messages.create(
            from_=settings.twilio_whatsapp_from,
            to=to_whatsapp,
            interactive=interactive,
        )
        return True
    except Exception:  # pragma: no cover
        logger.exception("Failed to send quick replies to %s", to_whatsapp)
        return False

