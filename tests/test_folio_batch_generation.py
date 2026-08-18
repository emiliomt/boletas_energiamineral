"""Folio batch creation: sequential + imported modes, collision rejection,
dedupe -- exercised through the real API (where the generation logic
lives) with a temp SQLite DB, same pattern as test_api_upload_and_review.py."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))

    from app.auth.session import require_admin_api, require_admin_web
    from app.main import app

    app.dependency_overrides[require_admin_api] = lambda: "test-admin@example.com"
    app.dependency_overrides[require_admin_web] = lambda: "test-admin@example.com"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_sequential_mode_generates_expected_folios(client):
    resp = client.post(
        "/api/folio-batches",
        json={"label": "Vendor Run 1", "mode": "sequential", "prefix": "B-", "start_number": 3201, "count": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 5

    detail = client.get(f"/api/folio-batches/{body['id']}").json()
    assert detail["issued_count"] == 5
    assert detail["scanned_count"] == 0


def test_imported_mode_accepts_unique_list(client):
    resp = client.post(
        "/api/folio-batches",
        json={"label": "Legacy Stock", "mode": "imported", "folios": ["A-1", "A-2", "A-3"]},
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


def test_imported_mode_rejects_duplicate_within_pasted_list(client):
    resp = client.post(
        "/api/folio-batches",
        json={"label": "Legacy Stock", "mode": "imported", "folios": ["A-1", "A-2", "A-1"]},
    )
    assert resp.status_code == 400
    assert "A-1" in resp.json()["detail"]


def test_imported_mode_rejects_empty_list(client):
    resp = client.post("/api/folio-batches", json={"label": "Empty", "mode": "imported", "folios": []})
    assert resp.status_code == 422  # FolioBatchCreate validator rejects empty folios


def test_sequential_mode_rejects_collision_with_existing_folio(client):
    client.post(
        "/api/folio-batches",
        json={"label": "First", "mode": "sequential", "prefix": "B-", "start_number": 4001, "count": 3},
    )
    resp = client.post(
        "/api/folio-batches",
        json={"label": "Overlapping", "mode": "sequential", "prefix": "B-", "start_number": 4002, "count": 2},
    )
    assert resp.status_code == 409
    assert "B-4002" in resp.json()["detail"]


def test_print_pdf_and_csv_downloads_work(client):
    resp = client.post(
        "/api/folio-batches",
        json={"label": "Downloads", "mode": "sequential", "prefix": "C-", "start_number": 1, "count": 2},
    )
    batch_id = resp.json()["id"]

    pdf_resp = client.get(f"/api/folio-batches/{batch_id}/print-pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content[:4] == b"%PDF"

    csv_resp = client.get(f"/api/folio-batches/{batch_id}/export-csv")
    assert csv_resp.status_code == 200
    assert "C-1" in csv_resp.text
    assert "C-2" in csv_resp.text
