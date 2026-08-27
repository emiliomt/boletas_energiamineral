"""Twilio WhatsApp webhook: signature, commands, media ingest, admin page."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from twilio.request_validator import RequestValidator

from app.models import Batch, Boleta, WhatsAppMessage, WhatsAppSession

AUTH_TOKEN = "test-twilio-token"
ACCOUNT_SID = "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SENDER = "whatsapp:+5491112345678"
WEBHOOK = "/webhooks/twilio/whatsapp"


def _png_bytes() -> bytes:
    image = Image.new("RGB", (32, 32), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sign(data: dict[str, str], url: str = f"http://testserver{WEBHOOK}") -> str:
    return RequestValidator(AUTH_TOKEN).compute_signature(url, data)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    monkeypatch.setattr(app_config.settings, "twilio_account_sid", ACCOUNT_SID)
    monkeypatch.setattr(app_config.settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(app_config.settings, "whatsapp_allowed_senders", "+5491112345678")
    monkeypatch.setattr(app_config.settings, "public_base_url", None)
    monkeypatch.setattr(app_config.settings, "twilio_whatsapp_from", "whatsapp:+14155238886")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", session_local)

    processed: list[int] = []

    def fake_process(boleta_ids: list[int]) -> None:
        processed.extend(boleta_ids)

    monkeypatch.setattr("app.whatsapp.webhook.process_boleta_ids", fake_process)

    from app.auth.session import require_admin_api, require_admin_web
    from app.main import app

    app.dependency_overrides[require_admin_api] = lambda: "test-admin@example.com"
    app.dependency_overrides[require_admin_web] = lambda: "test-admin@example.com"
    try:
        with TestClient(app) as c:
            yield c, session_local, processed
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, data: dict[str, str], url: str | None = None):
    signature_url = url or f"http://testserver{WEBHOOK}"
    return client.post(
        WEBHOOK,
        data=data,
        headers={"X-Twilio-Signature": _sign(data, signature_url)},
    )


def test_missing_signature_is_403(client):
    c, _, _ = client
    resp = c.post(WEBHOOK, data={"From": SENDER, "Body": "ayuda", "MessageSid": "SM1"})
    assert resp.status_code == 403


def test_invalid_signature_is_403(client):
    c, _, _ = client
    resp = c.post(
        WEBHOOK,
        data={"From": SENDER, "Body": "ayuda", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "not-valid"},
    )
    assert resp.status_code == 403


def test_ayuda_returns_twiml_help(client):
    c, _, _ = client
    resp = _post(c, {"From": SENDER, "Body": "ayuda", "MessageSid": "SMhelp", "NumMedia": "0"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "lote nuevo" in resp.text
    assert "<Message>" in resp.text


def test_unknown_sender_is_rejected(client):
    c, _, _ = client
    data = {"From": "whatsapp:+5491100000000", "Body": "ayuda", "MessageSid": "SMx", "NumMedia": "0"}
    resp = _post(c, data)
    assert resp.status_code == 200
    assert "no está autorizado" in resp.text


def test_photo_creates_scanning_batch_and_schedules_ocr(client, monkeypatch):
    c, session_local, processed = client
    png = _png_bytes()

    def fake_get(url, **kwargs):
        class Response:
            status_code = 200
            content = png
            headers = {"content-type": "image/jpeg"}

            def raise_for_status(self) -> None:
                return None

        return Response()

    monkeypatch.setattr("app.whatsapp.media.httpx.get", fake_get)

    data = {
        "From": SENDER,
        "Body": "",
        "MessageSid": "SMmedia1",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1",
        "MediaContentType0": "image/jpeg",
    }
    resp = _post(c, data)
    assert resp.status_code == 200
    assert "Recibí 1" in resp.text
    assert "lote de escaneo #" in resp.text

    db = session_local()
    try:
        batches = db.query(Batch).all()
        assert len(batches) == 1
        batch = batches[0]
        assert batch.notes == "whatsapp"
        assert batch.created_by == "+5491112345678"
        assert batch.status == "open"
        boletas = db.query(Boleta).filter_by(batch_id=batch.id).all()
        assert len(boletas) == 1
        assert boletas[0].document_type == "boleta"
        assert db.query(WhatsAppMessage).filter_by(message_sid="SMmedia1").count() == 1
        session = db.query(WhatsAppSession).filter_by(sender="+5491112345678").one()
        assert session.batch_id == batch.id
        boleta_id = boletas[0].id
    finally:
        db.close()

    assert processed == [boleta_id]


def test_second_photo_appends_to_same_batch(client, monkeypatch):
    c, session_local, processed = client
    png = _png_bytes()
    monkeypatch.setattr(
        "app.whatsapp.media.httpx.get",
        lambda url, **kwargs: type("R", (), {
            "status_code": 200,
            "content": png,
            "headers": {"content-type": "image/jpeg"},
            "raise_for_status": lambda self: None,
        })(),
    )
    _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SMa", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEa", "MediaContentType0": "image/jpeg",
    })
    _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SMb", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEb", "MediaContentType0": "image/jpeg",
    })
    db = session_local()
    try:
        assert db.query(Batch).count() == 1
        assert db.query(Boleta).count() == 2
    finally:
        db.close()
    assert len(processed) == 2


def test_duplicate_message_sid_is_idempotent(client, monkeypatch):
    c, session_local, processed = client
    png = _png_bytes()
    monkeypatch.setattr(
        "app.whatsapp.media.httpx.get",
        lambda url, **kwargs: type("R", (), {
            "status_code": 200,
            "content": png,
            "headers": {"content-type": "image/jpeg"},
            "raise_for_status": lambda self: None,
        })(),
    )
    payload = {
        "From": SENDER, "Body": "", "MessageSid": "SMdup", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEdup", "MediaContentType0": "image/jpeg",
    }
    first = _post(c, payload)
    second = _post(c, payload)
    assert first.status_code == 200
    assert "Ya recibí" in second.text
    db = session_local()
    try:
        assert db.query(Boleta).count() == 1
    finally:
        db.close()
    assert len(processed) == 1


def test_lote_nuevo_then_photo_uses_named_batch(client, monkeypatch):
    c, session_local, _ = client
    png = _png_bytes()
    monkeypatch.setattr(
        "app.whatsapp.media.httpx.get",
        lambda url, **kwargs: type("R", (), {
            "status_code": 200,
            "content": png,
            "headers": {"content-type": "image/jpeg"},
            "raise_for_status": lambda self: None,
        })(),
    )
    named = _post(c, {
        "From": SENDER, "Body": "lote nuevo Camión 12", "MessageSid": "SMcmd", "NumMedia": "0",
    })
    assert "Camión 12" in named.text
    _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SMpic", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEpic", "MediaContentType0": "image/jpeg",
    })
    db = session_local()
    try:
        batch = db.query(Batch).one()
        assert batch.label == "Camión 12"
        assert db.query(Boleta).filter_by(batch_id=batch.id).count() == 1
    finally:
        db.close()


def test_cfe_caption_tags_document_type(client, monkeypatch):
    c, session_local, _ = client
    png = _png_bytes()
    monkeypatch.setattr(
        "app.whatsapp.media.httpx.get",
        lambda url, **kwargs: type("R", (), {
            "status_code": 200,
            "content": png,
            "headers": {"content-type": "image/jpeg"},
            "raise_for_status": lambda self: None,
        })(),
    )
    resp = _post(c, {
        "From": SENDER, "Body": "cfe", "MessageSid": "SMcfe", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEcfe", "MediaContentType0": "image/jpeg",
    })
    assert "comprobante" in resp.text.lower()
    db = session_local()
    try:
        boleta = db.query(Boleta).one()
        assert boleta.document_type == "cfe_slip"
        session = db.query(WhatsAppSession).one()
        assert session.next_document_type == "boleta"  # flipped back after ingest
    finally:
        db.close()


def test_fin_closes_batch_so_next_photo_opens_another(client, monkeypatch):
    c, session_local, _ = client
    png = _png_bytes()
    monkeypatch.setattr(
        "app.whatsapp.media.httpx.get",
        lambda url, **kwargs: type("R", (), {
            "status_code": 200,
            "content": png,
            "headers": {"content-type": "image/jpeg"},
            "raise_for_status": lambda self: None,
        })(),
    )
    _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SM1", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1", "MediaContentType0": "image/jpeg",
    })
    closed = _post(c, {"From": SENDER, "Body": "fin", "MessageSid": "SMfin", "NumMedia": "0"})
    assert "Cerré" in closed.text
    _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SM2", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME2", "MediaContentType0": "image/jpeg",
    })
    db = session_local()
    try:
        batches = db.query(Batch).order_by(Batch.id).all()
        assert len(batches) == 2
        assert batches[0].status == "closed"
        assert batches[1].status == "open"
        assert db.query(Boleta).filter_by(batch_id=batches[0].id).count() == 1
        assert db.query(Boleta).filter_by(batch_id=batches[1].id).count() == 1
    finally:
        db.close()


def test_bind_existing_open_lote_by_id(client, monkeypatch):
    c, session_local, _ = client
    db = session_local()
    try:
        batch = Batch(label="Punto B", status="open")
        db.add(batch)
        db.commit()
        batch_id = batch.id
    finally:
        db.close()

    png = _png_bytes()
    monkeypatch.setattr(
        "app.whatsapp.media.httpx.get",
        lambda url, **kwargs: type("R", (), {
            "status_code": 200,
            "content": png,
            "headers": {"content-type": "image/jpeg"},
            "raise_for_status": lambda self: None,
        })(),
    )
    bind = _post(c, {"From": SENDER, "Body": f"lote {batch_id}", "MessageSid": "SMbind", "NumMedia": "0"})
    assert f"#{batch_id}" in bind.text
    _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SMinto", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEinto", "MediaContentType0": "image/jpeg",
    })
    db = session_local()
    try:
        assert db.query(Batch).count() == 1
        assert db.query(Boleta).filter_by(batch_id=batch_id).count() == 1
    finally:
        db.close()


def test_productor_switches_lote_to_entrada(client):
    c, session_local, _ = client
    resp = _post(c, {
        "From": SENDER, "Body": "productor Bradfort", "MessageSid": "SMprod", "NumMedia": "0",
    })
    assert resp.status_code == 200
    assert "Bradfort" in resp.text
    db = session_local()
    try:
        batch = db.query(Batch).one()
        assert batch.kind == "entrada"
        assert batch.producer_id is not None
    finally:
        db.close()


def test_signature_uses_public_base_url(client, monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "public_base_url", "https://boletas.example.com")
    c, _, _ = client
    data = {"From": SENDER, "Body": "ayuda", "MessageSid": "SMpub", "NumMedia": "0"}
    # Signed as Twilio would, against the public URL — not testserver.
    resp = _post(c, data, url="https://boletas.example.com" + WEBHOOK)
    assert resp.status_code == 200
    assert "lote nuevo" in resp.text


def test_rejected_video_does_not_create_boleta(client, monkeypatch):
    c, session_local, processed = client
    data = {
        "From": SENDER, "Body": "", "MessageSid": "SMvid", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/MEvid", "MediaContentType0": "video/mp4",
    }
    resp = _post(c, data)
    assert resp.status_code == 200
    assert "No puedo procesar" in resp.text
    db = session_local()
    try:
        assert db.query(Boleta).count() == 0
    finally:
        db.close()
    assert processed == []


def test_admin_whatsapp_page_shows_webhook(client, monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "public_base_url", "https://boletas.example.com")
    c, _, _ = client
    html = c.get("/admin/whatsapp").text
    assert "Subir boletas por WhatsApp" in html
    assert "https://boletas.example.com/webhooks/twilio/whatsapp" in html
    assert "Twilio está configurado" in html
    assert "+5491112345678" in html


def test_admin_whatsapp_requires_login(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "auth.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False))

    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/admin/whatsapp", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")
