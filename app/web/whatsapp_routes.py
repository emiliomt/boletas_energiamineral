"""Admin page: how to point Twilio WhatsApp at this app, plus live sessions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR, settings
from app.db import get_db
from app.models import Batch, WhatsAppSession
from app.whatsapp.commands import HELP_TEXT
from app.whatsapp.webhook import TWILIO_WEBHOOK_PATH

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


def _webhook_public_url() -> str | None:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}{TWILIO_WEBHOOK_PATH}"


@router.get("/admin/whatsapp")
def whatsapp_admin(request: Request, db: Session = Depends(get_db)):
    configured = bool(settings.twilio_account_sid and settings.twilio_auth_token)
    allowlist = [
        item.strip()
        for item in (settings.whatsapp_allowed_senders or "").split(",")
        if item.strip()
    ]
    sessions = (
        db.query(WhatsAppSession)
        .order_by(WhatsAppSession.updated_at.desc())
        .limit(50)
        .all()
    )
    batches_by_id = {}
    batch_ids = [s.batch_id for s in sessions if s.batch_id]
    if batch_ids:
        batches_by_id = {b.id: b for b in db.query(Batch).filter(Batch.id.in_(batch_ids)).all()}
    return templates.TemplateResponse(
        request,
        "whatsapp.html",
        {
            "configured": configured,
            "allowlist": allowlist,
            "webhook_path": TWILIO_WEBHOOK_PATH,
            "webhook_url": _webhook_public_url(),
            "whatsapp_from": settings.twilio_whatsapp_from,
            "sessions": sessions,
            "batches_by_id": batches_by_id,
            "help_text": HELP_TEXT,
        },
    )
