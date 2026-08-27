"""Turn a validated Twilio WhatsApp payload into scanning-batch uploads."""
from __future__ import annotations

import datetime as dt
import logging
import re

from rapidfuzz import fuzz, process as fuzz_process
from sqlalchemy.orm import Session

from app.ingestion.storage import store_upload
from app.models import Batch, Producer, WhatsAppMessage, WhatsAppSession
from app.whatsapp.commands import HELP_TEXT, Command, parse_command
from app.whatsapp.media import download_media, parse_media_items
from app.whatsapp.numbers import normalize_sender

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^\d+$")


class HandleResult:
    def __init__(self, reply: str, boleta_ids: list[int] | None = None) -> None:
        self.reply = reply
        self.boleta_ids = boleta_ids or []


def _now_label() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _get_session(db: Session, sender: str) -> WhatsAppSession:
    row = db.query(WhatsAppSession).filter_by(sender=sender).one_or_none()
    if row is None:
        row = WhatsAppSession(sender=sender, next_document_type="boleta")
        db.add(row)
        db.flush()
    return row


def _batch_summary(batch: Batch) -> str:
    kind = "Entrada" if batch.kind == "entrada" else "Salida"
    return f"lote de escaneo #{batch.id} «{batch.label}» ({kind})"


def ensure_open_batch(db: Session, session: WhatsAppSession, sender: str, label: str | None = None) -> Batch:
    if session.batch_id:
        batch = db.get(Batch, session.batch_id)
        if batch is not None and batch.status == "open":
            return batch
    batch = Batch(
        label=label or f"WhatsApp {_now_label()}",
        created_by=sender,
        notes="whatsapp",
        kind="salida",
        status="open",
    )
    db.add(batch)
    db.flush()
    session.batch_id = batch.id
    return batch


def _create_batch(db: Session, session: WhatsAppSession, sender: str, label: str | None) -> Batch:
    batch = Batch(
        label=label or f"WhatsApp {_now_label()}",
        created_by=sender,
        notes="whatsapp",
        kind="salida",
        status="open",
    )
    db.add(batch)
    db.flush()
    session.batch_id = batch.id
    session.next_document_type = "boleta"
    return batch


def _bind_lote(db: Session, session: WhatsAppSession, sender: str, arg: str) -> str:
    arg = arg.strip()
    if _ID_RE.fullmatch(arg):
        batch = db.get(Batch, int(arg))
        if batch is None:
            return f"No encontré el lote de escaneo #{arg}. Creá uno con «lote nuevo»."
        if batch.status != "open":
            return f"El lote #{batch.id} «{batch.label}» está cerrado. Abrí otro con «lote nuevo»."
        session.batch_id = batch.id
        return f"Listo. Las próximas fotos van a {_batch_summary(batch)}."

    open_named = (
        db.query(Batch)
        .filter(Batch.label == arg, Batch.status == "open")
        .order_by(Batch.id.desc())
        .first()
    )
    if open_named is not None:
        session.batch_id = open_named.id
        return f"Listo. Las próximas fotos van a {_batch_summary(open_named)}."

    batch = _create_batch(db, session, sender, arg)
    return f"Abrí {_batch_summary(batch)}. Mandá las fotos de las boletas."


def _match_producer(db: Session, name: str) -> Producer | None:
    producers = db.query(Producer).filter_by(active=True).all()
    if not producers:
        return None
    lowered = name.strip().lower()
    for producer in producers:
        if producer.name.lower() == lowered:
            return producer
    choices = [p.name for p in producers]
    match = fuzz_process.extractOne(name, choices, scorer=fuzz.WRatio)
    if match is None or match[1] < 80:
        return None
    by_name = {p.name: p for p in producers}
    return by_name.get(match[0])


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


def _ingest_media(
    db: Session,
    session: WhatsAppSession,
    sender: str,
    params: dict[str, str],
    document_type: str,
) -> tuple[str, list[int]]:
    items = parse_media_items(params)
    if not items:
        return ("No vino ninguna foto. Mandá una foto de la boleta.", [])

    sid = (params.get("MessageSid") or "wa").strip()
    batch = ensure_open_batch(db, session, sender)
    stored: list[int] = []
    skipped: list[str] = []

    for index, (url, content_type) in enumerate(items):
        result = download_media(url, content_type, filename_stem=f"wa_{sid}_{index}")
        if isinstance(result, str):
            skipped.append(result)
            continue
        boletas = store_upload(db, batch, result.filename, result.content, result.mime_type, document_type)
        stored.extend(b.id for b in boletas)

    # After a CFE photo, flip back to boleta so a mixed pair doesn't need two commands.
    if document_type == "cfe_slip":
        session.next_document_type = "boleta"

    if not stored:
        return (skipped[0] if skipped else "No pude guardar las fotos. Mandalas de nuevo.", [])

    noun = "comprobante(s) CFE" if document_type == "cfe_slip" else "boleta(s)"
    reply = (
        f"Recibí {len(stored)} {noun} en {_batch_summary(batch)}. "
        "Las estoy procesando; en un momento aparecen en el lote."
    )
    if skipped:
        reply += " Algunas no se pudieron guardar: " + " ".join(skipped)
    return (reply, stored)


def handle_inbound(db: Session, params: dict[str, str]) -> HandleResult:
    """Apply commands and/or store media. Caller commits and schedules OCR."""
    sender = normalize_sender(params.get("From") or "")
    if not sender:
        return HandleResult("No pude leer el número de WhatsApp.")

    sid = (params.get("MessageSid") or "").strip()
    if sid:
        existing = db.query(WhatsAppMessage).filter_by(message_sid=sid).one_or_none()
        if existing is not None:
            return HandleResult("Ya recibí ese mensaje. Si falta una foto, mandala de nuevo.")

    session = _get_session(db, sender)
    body = (params.get("Body") or "").strip()
    command = parse_command(body)
    items = parse_media_items(params)

    replies: list[str] = []
    boleta_ids: list[int] = []
    document_type = session.next_document_type or "boleta"

    if command is not None:
        if command.kind == "doc_type" and items:
            document_type = command.arg or "boleta"
            session.next_document_type = document_type
        else:
            replies.append(_apply_command(db, session, sender, command))
            document_type = session.next_document_type or "boleta"

    if items:
        media_reply, boleta_ids = _ingest_media(db, session, sender, params, document_type)
        replies.append(media_reply)
    elif command is None:
        if body:
            replies.append("No entendí. Escribí ayuda o mandá una foto de la boleta.")
        else:
            replies.append(HELP_TEXT)

    if sid:
        db.add(
            WhatsAppMessage(
                message_sid=sid,
                sender=sender,
                batch_id=session.batch_id,
                media_count=len(boleta_ids),
            )
        )

    return HandleResult("\n\n".join(r for r in replies if r), boleta_ids)
