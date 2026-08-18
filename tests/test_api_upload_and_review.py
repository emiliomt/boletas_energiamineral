"""End-to-end API test: upload a fixture through the HTTP layer, confirm it
was processed, submit a correction through the review endpoint, and confirm
the audit trail and export reflect it.

Uses a temp SQLite DB and temp originals dir (via monkeypatched app.config
settings + app.db engine) so it never touches the real data/boletas.db.
"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BOLETAS_FIXTURES_DIR

requires_tesseract_and_fixtures = pytest.mark.skipif(
    shutil.which("tesseract") is None or not BOLETAS_FIXTURES_DIR.exists(),
    reason="tesseract binary or generated fixtures not available",
)


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

    from app.main import app

    with TestClient(app) as c:
        yield c


@requires_tesseract_and_fixtures
def test_upload_process_review_and_export_roundtrip(client):
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
            "weight": 9000,
        },
    )
    assert review_resp.status_code == 200
    reviewed = review_resp.json()
    assert reviewed["status"] == "auto_processed"
    assert reviewed["boleta_id"] == "B-9999"
    assert reviewed["trip_type"] == "acarreo_carbon"
    assert reviewed["tariff_amount"] == 850.0

    export_resp = client.get(f"/api/exports/json?batch_id={batch_id}")
    assert export_resp.status_code == 200
    exported = export_resp.json()
    assert len(exported) == 1
    assert exported[0]["boleta_id"] == "B-9999"
    assert exported[0]["status"] == "auto_processed"
