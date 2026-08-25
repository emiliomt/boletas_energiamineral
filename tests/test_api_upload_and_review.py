"""End-to-end API tests: upload fixtures/synthetic scans through the HTTP
layer, confirm processing, and exercise the review endpoint -- including,
since Phase 3, that a Salida record still missing its CFE slip/boleta
counterpart can't be force-completed via manual approval (only real
reconciliation completes it).

Uses a temp SQLite DB and temp originals dir (via monkeypatched app.config
settings + app.db engine) so it never touches the real data/boletas.db.
"""
from __future__ import annotations

import io
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from tests.conftest import BOLETAS_FIXTURES_DIR

requires_tesseract_and_fixtures = pytest.mark.skipif(
    shutil.which("tesseract") is None or not BOLETAS_FIXTURES_DIR.exists(),
    reason="tesseract binary or generated fixtures not available",
)
requires_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not available")


def _font(size: int = 22):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _render_text_image(lines: list[str]) -> bytes:
    canvas = Image.new("RGB", (900, 400), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font()
    y = 30
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 42
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    # app.db built its engine/session at import time from the original
    # settings, so point them at the same temp DB for this test.
    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))

    from app.auth.session import require_admin_api, require_admin_web
    from app.main import app

    # Bypass real Supabase Auth in tests -- auth itself is covered by
    # tests/test_auth.py; this fixture just needs an authenticated session
    # to exercise the rest of the API.
    app.dependency_overrides[require_admin_api] = lambda: "test-admin@example.com"
    app.dependency_overrides[require_admin_web] = lambda: "test-admin@example.com"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@requires_tesseract
def test_upload_both_documents_completes_reviews_and_exports(client):
    folio_batch_resp = client.post(
        "/api/folio-batches", json={"label": "test-folios", "mode": "imported", "folios": ["B-8001"]}
    )
    assert folio_batch_resp.status_code == 200

    batch_resp = client.post("/api/batches", json={"label": "test-batch"})
    assert batch_resp.status_code == 200
    batch_id = batch_resp.json()["id"]

    boleta_png = _render_text_image(
        [
            "Folio: B-8001",
            "Fecha: 20/01/2026",
            "Centro de Explotacion: Mina San Jose",
            "Destino: Planta Norte",
            "Datos del chofer del camion: Juan Perez",
        ]
    )
    slip_png = _render_text_image(
        ["Folio: B-8001", "Fecha: 20/01/2026", "Peso de Entrada: 500 kg", "Peso de Salida: 9500 kg"]
    )

    upload_resp = client.post(
        f"/api/batches/{batch_id}/upload",
        files=[("files", ("boleta.png", boleta_png, "image/png"))],
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["processed"][0]["status"] == "needs_review"  # boleta_only, waiting

    upload_resp = client.post(
        f"/api/batches/{batch_id}/upload",
        files=[("cfe_slip_files", ("slip.png", slip_png, "image/png"))],
    )
    assert upload_resp.status_code == 200
    body = upload_resp.json()
    record_id = body["processed"][0]["record_id"]
    assert body["processed"][0]["status"] == "auto_processed"

    record_resp = client.get(f"/api/records/{record_id}")
    assert record_resp.status_code == 200
    record = record_resp.json()
    assert record["boleta_id"] == "B-8001"
    assert record["trip_type"] == "acarreo_carbon"
    assert record["tariff_amount"] == 900.0  # 0.10 MXN/kg * 9000kg delivered, PricingRule P004
    assert record["salida_status"] == "complete"
    assert record["delivered_weight"] == 9000.0

    # Superseded (the earlier boleta-only row) must not leak into listings.
    export_resp = client.get(f"/api/exports/json?batch_id={batch_id}")
    assert export_resp.status_code == 200
    exported = export_resp.json()
    assert len(exported) == 1
    assert exported[0]["boleta_id"] == "B-8001"
    assert exported[0]["status"] == "auto_processed"


@requires_tesseract_and_fixtures
def test_correcting_and_approving_a_partial_salida_record_cannot_force_complete(client):
    """Since Phase 3: a Salida record can be corrected via review while it
    waits on its counterpart document, but "approve" must not force it to
    auto_processed -- only real reconciliation (the matching CFE slip
    actually arriving) completes it. See app/review/service.py::apply_review.
    """
    batch_resp = client.post("/api/batches", json={"label": "test-batch"})
    assert batch_resp.status_code == 200
    batch_id = batch_resp.json()["id"]

    with (BOLETAS_FIXTURES_DIR / "sample_boleta_03_illegible.png").open("rb") as f:
        upload_resp = client.post(
            f"/api/batches/{batch_id}/upload",
            files={"files": ("sample_boleta_03_illegible.png", f, "image/png")},
        )
    assert upload_resp.status_code == 200
    body = upload_resp.json()
    assert body["count"] == 1
    record_id = body["processed"][0]["record_id"]
    assert body["processed"][0]["status"] == "needs_review"

    queue_resp = client.get("/api/review-queue")
    assert queue_resp.status_code == 200
    assert any(r["record_id"] == record_id for r in queue_resp.json())

    review_resp = client.post(
        f"/api/records/{record_id}/review",
        json={
            "action": "approve",
            "edited_by": "tester",
            "folio": "B-9999",
            "date": "2026-01-20",
            "origin": "Mina San Jose",
            "destination": "Planta Norte",
            "fletero": "Juan Perez",
        },
    )
    assert review_resp.status_code == 200
    reviewed = review_resp.json()
    # Corrections still apply...
    assert reviewed["boleta_id"] == "B-9999"
    assert reviewed["trip_type"] == "acarreo_carbon"
    # ...but approval can't manufacture a priced/inventoried outcome for a
    # record still missing its CFE slip.
    assert reviewed["status"] == "needs_review"
    assert reviewed["salida_status"] == "boleta_only"
    assert reviewed["tariff_amount"] is None
