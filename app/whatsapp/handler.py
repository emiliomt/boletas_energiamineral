"""Gate-first WhatsApp ingest flow.

Validated Twilio WhatsApp webhook is handled here:
- Photo arrival only stores media and starts a Q&A (awaiting_meta)
- Ask in order: Lote (registered folios), Entrada/Salida, Interna/CFE
- After all three: create Boleta in a scanning lote and queue OCR
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy.orm import Session

from pathlib import Path
from app.ingestion.storage import store_upload
from app.config import BASE_DIR
from app.models import (
    Batch,
    FolioBatch,
    WhatsAppConversation,
    WhatsAppIngest,
    WhatsAppMessage,
)
from app.whatsapp.media import download_media, parse_media_items
from app.whatsapp.numbers import normalize_sender
from app.whatsapp import send as wa_send
import json

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^\d+$")


class HandleResult:
    def __init__(self, reply: str, boleta_ids: list[int] | None = None) -> None:
        self.reply = reply
        self.boleta_ids = boleta_ids or []


def _now_label() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _get_conversation(db: Session, sender: str):
    row = db.query(WhatsAppConversation).filter_by(sender=sender).one_or_none()
    if row is None:
        row = WhatsAppConversation(sender=sender, step="ask_lote")
        db.add(row)
        db.flush()
    return row


def _batch_summary(batch: Batch) -> str:
    kind = "Entrada" if batch.kind == "entrada" else "Salida"
    return f"lote de escaneo #{batch.id} «{batch.label}» ({kind})"


def _ensure_open_batch_for_label(db: Session, sender: str, label: str, kind: str) -> Batch:
    batch = (
        db.query(Batch)
        .filter(Batch.label == label, Batch.status == "open")
        .order_by(Batch.id.desc())
        .first()
    )
    if batch is not None:
        return batch
    batch = Batch(label=label, created_by=sender, notes="whatsapp", kind=("entrada" if kind == "entrada" else "salida"))
    db.add(batch)
    db.flush()
    return batch


def _save_downloaded_media(result_filename: str, content: bytes) -> Path:
    """Persist inbound WhatsApp media to a local data folder and return path."""
    base = BASE_DIR / "data" / "whatsapp_media"
    base.mkdir(parents=True, exist_ok=True)
    out_path = base / result_filename
    if out_path.exists():
        stem = out_path.stem
        suffix = out_path.suffix
        i = 1
        while True:
            candidate = base / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                out_path = candidate
                break
            i += 1
    out_path.write_bytes(content)
    return out_path


def _prompt_lote(db: Session, sender: str) -> str:
    lotes = db.query(FolioBatch).filter(FolioBatch.deleted_at.is_(None)).order_by(FolioBatch.id.desc()).all()
    if not lotes:
        return "No hay lotes registrados. Primero registrá un lote en la app."
    # Attempt interactive list; fallback text always returned below.
    try:
        rows = [{"id": f"lote:{lb.id}", "title": lb.label, "description": ""} for lb in lotes[:10]]
        wa_send.send_interactive_lote_list(sender, rows)
    except Exception:
        logger.exception("Failed to enqueue interactive lote list")
    preview = ", ".join(f"#{lb.id} {lb.label}" for lb in lotes[:10])
    return ("¿Qué lote de folios usaste? Respondé con el ID o nombre exacto.\n"
            f"Opciones: {preview}")


def _apply_text_reply(db: Session, conv: WhatsAppConversation, sender: str, body: str) -> tuple[str, list[int]]:
    """Advance the Q&A state machine on text replies; never invent values."""
    step = conv.step or "ask_lote"
    body_l = (body or "").strip().lower()
    ingest = db.get(WhatsAppIngest, conv.ingest_id) if conv.ingest_id else None
    if ingest is None:
        conv.step = "ask_lote"
        return "Mandá una foto de la boleta para empezar.", []
    if step == "ask_lote":
        lotes = db.query(FolioBatch).filter(FolioBatch.deleted_at.is_(None)).all()
        if not lotes:
            return "No hay lotes registrados. Primero registrá un lote en la app.", []
        selected_id: int | None = None
        # Accept interactive payloads like "lote:123"
        if body_l.startswith("lote:") and body_l.split(":", 1)[1].isdigit():
            selected_id = int(body_l.split(":", 1)[1])
        if _ID_RE.fullmatch(body_l):
            fid = int(body_l)
            match = next((lb for lb in lotes if lb.id == fid), None)
            if match:
                selected_id = match.id
        else:
            match = next((lb for lb in lotes if lb.label.lower() == body_l), None)
            if match:
                selected_id = match.id
        if selected_id is None:
            return _prompt_lote(db, sender), []
        ingest.lote_id = selected_id
        conv.step = "ask_movimiento"
        # Try interactive quick replies
        wa_send.send_quick_replies(sender, "¿Entrada o salida?", [("mov:entrada", "Entrada"), ("mov:salida", "Salida")])
        return "¿Entrada o salida? (respondé «entrada» o «salida»)", []
    if step == "ask_movimiento":
        # Accept interactive payloads like "mov:entrada"
        if body_l.startswith("mov:"):
            body_l = body_l.split(":", 1)[1]
        if body_l not in {"entrada", "salida"}:
            return "No entendí. ¿Entrada o salida? (respondé «entrada» o «salida»)", []
        ingest.movimiento = body_l
        conv.step = "ask_fuente"
        wa_send.send_quick_replies(sender, "¿Boleta interna o CFE?", [("fuente:interna", "Interna"), ("fuente:cfe", "CFE")])
        return "¿Boleta interna o CFE? (respondé «interna» o «cfe»)", []
    if step == "ask_fuente":
        if body_l.startswith("fuente:"):
            body_l = body_l.split(":", 1)[1]
        if body_l not in {"interna", "cfe"}:
            return "No entendí. ¿Boleta interna o CFE? (respondé «interna» o «cfe»)", []
        ingest.fuente = body_l
        folio_batch = db.get(FolioBatch, ingest.lote_id) if ingest.lote_id else None
        label = folio_batch.label if folio_batch else f"WhatsApp {_now_label()}"
        kind = ingest.movimiento or "salida"
        batch = _ensure_open_batch_for_label(db, sender, label, kind)
        document_type = "cfe_slip" if ingest.fuente == "cfe" else "boleta"
        file_path = Path(ingest.media_url)
        try:
            content = file_path.read_bytes()
        except Exception:
            logger.exception("Failed to read stored media %s", file_path)
            return "No pude leer la foto guardada. Mandala de nuevo.", []
        boletas = store_upload(
            db,
            batch,
            file_path.name,
            content,
            "application/pdf" if file_path.suffix.lower() == ".pdf" else "image/jpeg",
            document_type,
        )
        ingest.status = "processing"
        if boletas:
            ingest.created_boleta_id = boletas[0].id
        conv.step = "done"
        return (
            f"Listo. Guardé tu {'comprobante CFE' if document_type=='cfe_slip' else 'boleta'} en "
            f"{_batch_summary(batch)}. La estoy procesando."
        ), ([boletas[0].id] if boletas else [])
    return "No entendí. Mandá una foto de la boleta para empezar.", []


def _apply_command(db: Session, session: WhatsAppSession, sender: str, command: Command) -> str:
    if command.kind == "help":
        return HELP_TEXT
    if command.kind == "status":
        if not session.batch_id:
            return "No hay un lote abierto. Mandá una foto o escribí «lote nuevo»."
        batch = db.get(Batch, session.batch_id)
        if batch is None:
            session.batch_id = None
            return "No hay un lote abierto. Mandá una foto o escribí «lote nuevo»."
        count = len(batch.boletas)
        doc = "comprobante CFE" if session.next_document_type == "cfe_slip" else "boleta"
        extra = ""
        if batch.kind == "entrada":
            producer = db.get(Producer, batch.producer_id) if batch.producer_id else None
            extra = f" Productor: {producer.name}." if producer else " Falta productor (escribí «productor NOMBRE»)."
        return (
            f"Lote actual: {_batch_summary(batch)}. "
            f"{count} archivo(s). Próxima foto se guarda como {doc}.{extra}"
        )
    if command.kind == "close":
        if not session.batch_id:
            return "No había un lote abierto."
        batch = db.get(Batch, session.batch_id)
        if batch is not None and batch.status == "open":
            batch.status = "closed"
            reply = f"Cerré {_batch_summary(batch)}. Las siguientes fotos abren un lote nuevo."
        else:
            reply = "No había un lote abierto."
        session.batch_id = None
        session.next_document_type = "boleta"
        return reply
    if command.kind == "doc_type":
        session.next_document_type = command.arg or "boleta"
        if session.next_document_type == "cfe_slip":
            return "La próxima foto se guarda como comprobante CFE."
        return "La próxima foto se guarda como boleta."
    if command.kind == "set_kind":
        batch = ensure_open_batch(db, session, sender)
        kind = command.arg if command.arg in {"entrada", "salida"} else "salida"
        batch.kind = kind
        if kind == "salida":
            batch.producer_id = None
            return f"{_batch_summary(batch)} quedó como Salida. Mandá las fotos."
        producer = db.get(Producer, batch.producer_id) if batch.producer_id else None
        if producer:
            return f"{_batch_summary(batch)} quedó como Entrada, productor {producer.name}."
        return (
            f"{_batch_summary(batch)} quedó como Entrada. "
            "Escribí «productor NOMBRE» antes de mandar las fotos."
        )
    if command.kind == "set_producer":
        batch = ensure_open_batch(db, session, sender)
        producer = _match_producer(db, command.arg or "")
        if producer is None:
            names = [p.name for p in db.query(Producer).filter_by(active=True).order_by(Producer.name).all()]
            hint = f" Productores: {', '.join(names)}." if names else " No hay productores cargados en el sistema."
            return f"No reconocí el productor «{command.arg}».{hint}"
        batch.producer_id = producer.id
        if batch.kind != "entrada":
            batch.kind = "entrada"
        return f"{_batch_summary(batch)} usa el productor {producer.name}."
    if command.kind == "new_lote":
        batch = _create_batch(db, session, sender, command.arg)
        return f"Abrí {_batch_summary(batch)}. Mandá las fotos de las boletas."
    if command.kind == "bind_lote":
        return _bind_lote(db, session, sender, command.arg or "")
    return HELP_TEXT


def _start_ingest_from_media(db: Session, conv: WhatsAppConversation, sender: str, params: dict[str, str]) -> str:
    items = parse_media_items(params)
    if not items:
        return "No vino ninguna foto. Mandá una foto de la boleta."
    sid = (params.get("MessageSid") or "wa").strip()
    url, content_type = items[0]
    result = download_media(url, content_type, filename_stem=f"wa_{sid}_0")
    if isinstance(result, str):
        return result
    path = _save_downloaded_media(result.filename, result.content)
    ingest = WhatsAppIngest(
        lote_id=None,
        movimiento=None,
        fuente=None,
        media_url=str(path),
        status="awaiting_meta",
        whatsapp_from=sender,
        received_at=dt.datetime.now(dt.timezone.utc),
        message_sid=sid or None,
    )
    db.add(ingest)
    db.flush()
    conv.ingest_id = ingest.id
    conv.step = "ask_lote"
    return _prompt_lote(db, sender)


def handle_inbound(db: Session, params: dict[str, str]) -> HandleResult:
    """Gate-first flow. Caller commits; OCR is queued only after meta is complete."""
    sender = normalize_sender(params.get("From") or "")
    if not sender:
        return HandleResult("No pude leer el número de WhatsApp.")

    sid = (params.get("MessageSid") or "").strip()
    if sid:
        existing = db.query(WhatsAppMessage).filter_by(message_sid=sid).one_or_none()
        if existing is not None:
            return HandleResult("Ya recibí ese mensaje. Si falta una foto, mandala de nuevo.")

    conv = _get_conversation(db, sender)
    body = (params.get("Body") or "").strip()
    # Parse interactive replies first (Twilio passes InteractiveData JSON and/or ButtonPayload/Text)
    raw_interactive = (params.get("InteractiveData") or "").strip()
    try:
        if raw_interactive:
            data = json.loads(raw_interactive)
            t = (data.get("interactive") or {}).get("type")
            if t == "list_reply":
                lr = (data.get("interactive") or {}).get("list_reply") or {}
                body = lr.get("id") or lr.get("title") or body
            elif t == "button_reply":
                br = (data.get("interactive") or {}).get("button_reply") or {}
                body = br.get("id") or br.get("title") or body
    except Exception:
        # ignore parse errors; fallback to plain Body/Button*
        pass
    if not raw_interactive:
        payload = (params.get("ButtonPayload") or "").strip()
        if payload:
            body = payload
    items = parse_media_items(params)

    replies: list[str] = []
    boleta_ids: list[int] = []

    if items:
        replies.append(_start_ingest_from_media(db, conv, sender, params))
    else:
        if body:
            msg, created = _apply_text_reply(db, conv, sender, body)
            replies.append(msg)
            boleta_ids.extend(created)
        else:
            replies.append("Mandá una foto de la boleta para empezar.")

    if sid:
        db.add(
            WhatsAppMessage(
                message_sid=sid,
                sender=sender,
                batch_id=None,
                media_count=len(boleta_ids),
            )
        )

    return HandleResult("\n\n".join(r for r in replies if r), boleta_ids)
