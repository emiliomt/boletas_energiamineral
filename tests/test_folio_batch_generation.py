"""Folio batch creation: sequential + imported modes, collision rejection,
dedupe -- exercised through the real API (where the generation logic
lives) with a temp SQLite DB, same pattern as test_api_upload_and_review.py."""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

# Batch-level boleta data entered online before printing.
ONLINE_FIELDS = {
    "proveedor": "ENERGIA MINERAL, S.A. DE C.V.",
    "destino": "C.T. Jose Lopez Portillo",
    "contrato": "700544405",
    "poder_calorifico_superior": "6200",
    "humedad_pct": "8.5",
    "ceniza_pct": "12",
    "azufre_pct": "0.8",
    "fsi": "4",
    "granulometria": "50mm",
    "centro_explotacion": "Tajo San Jose",
    "centro_acopio": "Patio Rosita",
    "concesion_minera": "CONC-2201",
    "representante_legal": "Maria Lopez",
}


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


def test_online_boleta_fields_are_stored_and_returned(client):
    resp = client.post(
        "/api/folio-batches",
        json={
            "label": "Semana 41",
            "mode": "sequential",
            "prefix": "B-",
            "start_number": 5001,
            "count": 1,
            **ONLINE_FIELDS,
        },
    )
    assert resp.status_code == 200
    batch_id = resp.json()["id"]

    detail = client.get(f"/api/folio-batches/{batch_id}").json()
    for field, value in ONLINE_FIELDS.items():
        assert detail[field] == value, field


def test_web_form_stores_online_fields(client):
    resp = client.post(
        "/admin/folio-batches",
        data={
            "label": "Semana 42",
            "mode": "sequential",
            "prefix": "W-",
            "start_number": 6001,
            "count": 1,
            **ONLINE_FIELDS,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Find the created batch via the API and confirm the fields persisted.
    batches = client.get("/api/folio-batches").json()
    batch = next(b for b in batches if b["label"] == "Semana 42")
    for field, value in ONLINE_FIELDS.items():
        assert batch[field] == value, field


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not available")
def test_generated_pdf_prints_online_fields(client, tmp_path):
    from app.ingestion.pdf_split import split_pdf_to_images
    from app.ocr.tesseract_adapter import TesseractOCRAdapter

    resp = client.post(
        "/api/folio-batches",
        json={
            "label": "Semana 43",
            "mode": "sequential",
            "prefix": "P-",
            "start_number": 7001,
            "count": 1,
            **ONLINE_FIELDS,
        },
    )
    batch_id = resp.json()["id"]

    pdf_resp = client.get(f"/api/folio-batches/{batch_id}/print-pdf")
    assert pdf_resp.content[:4] == b"%PDF"

    pdf_path = tmp_path / "boleta.pdf"
    pdf_path.write_bytes(pdf_resp.content)
    page = split_pdf_to_images(pdf_path, tmp_path)[0]
    text = TesseractOCRAdapter().extract(page).text

    # Pre-printed online values are present on the generated boleta.
    for needle in ["P-7001", "Jose Lopez Portillo", "700544405", "Tajo San Jose", "Patio Rosita", "Maria Lopez"]:
        assert needle in text, needle

    # The per-trip fields (filled by hand + OCR'd later) still render their
    # labels; they just have no value printed.
    for label in ["Fecha:", "No. Caja:", "Firma:"]:
        assert label in text, label


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
