"""Gate-first WhatsApp ingest tests: photo -> awaiting_meta -> lote -> movimiento -> fuente."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from twilio.request_validator import RequestValidator

from app.models import Batch, Boleta, FolioBatch, WhatsAppIngest, WhatsAppMessage

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
    # Disable real Twilio sends
    monkeypatch.setattr("app.whatsapp.send.send_interactive_lote_list", lambda to, rows: True)
    monkeypatch.setattr("app.whatsapp.send.send_quick_replies", lambda to, prompt, buttons: True)

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


def test_photo_prompts_lote_and_defers_processing(client, monkeypatch):
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
    resp = _post(c, {
        "From": SENDER, "Body": "", "MessageSid": "SM1", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1", "MediaContentType0": "image/jpeg",
    })
    assert resp.status_code == 200
    # When no FolioBatches exist yet, we instruct to register one first.
    assert ("¿Qué lote de folios usaste?" in resp.text) or ("No hay lotes registrados" in resp.text)
    db = session_local()
    try:
        assert db.query(WhatsAppIngest).count() == 1
        assert db.query(Batch).count() == 0
        assert db.query(Boleta).count() == 0
        assert db.query(WhatsAppMessage).filter_by(message_sid="SM1").count() == 1
    finally:
        db.close()
    assert processed == []


def test_full_gate_first_flow(client, monkeypatch):
    c, session_local, processed = client
    db = session_local()
    try:
        fb = FolioBatch(label="Lote B", mode="sequential", prefix="B-", start_number=1, count=5)
        db.add(fb)
        db.commit()
        lote_id = fb.id
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
    # Photo
    _post(c, {"From": SENDER, "Body": "", "MessageSid": "SMg1", "NumMedia": "1", "MediaUrl0": "https://api.twilio.com/media/ME1", "MediaContentType0": "image/jpeg"})
    # Lote
    r1 = _post(c, {"From": SENDER, "Body": str(lote_id), "MessageSid": "SMg2", "NumMedia": "0"})
    assert "Entrada o salida" in r1.text
    # Movimiento
    r2 = _post(c, {"From": SENDER, "Body": "entrada", "MessageSid": "SMg3", "NumMedia": "0"})
    assert "Boleta interna o CFE" in r2.text
    # Fuente
    r3 = _post(c, {"From": SENDER, "Body": "interna", "MessageSid": "SMg4", "NumMedia": "0"})
    assert "Guardé tu boleta" in r3.text
    db = session_local()
    try:
        assert db.query(Batch).count() == 1
        assert db.query(Boleta).count() == 1
        boleta_id = db.query(Boleta).one().id
    finally:
        db.close()
    assert processed == [boleta_id]

