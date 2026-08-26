"""Template contracts for the server-rendered UI: routes, field names, and
landmarks stay stable even as presentation changes."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR
from app.models import Batch, Boleta, BoletaRecord, FolioBatch


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", session_local)

    from app.auth.session import require_admin_api, require_admin_web
    from app.main import app

    app.dependency_overrides[require_admin_api] = lambda: "test-admin@example.com"
    app.dependency_overrides[require_admin_web] = lambda: "test-admin@example.com"
    try:
        with TestClient(app) as c:
            yield c, session_local
    finally:
        app.dependency_overrides.clear()


def test_static_assets_are_served(client):
    c, _ = client
    css = c.get("/static/style.css")
    js = c.get("/static/app.js")
    assert css.status_code == 200
    assert "prefers-reduced-motion" in css.text
    assert ":focus-visible" in css.text
    assert "--primary:" in css.text
    assert js.status_code == 200


def test_login_page_preserves_auth_contract(client):
    c, _ = client
    html = c.get("/login?next=/dashboard").text
    assert 'name="viewport"' in html
    assert 'method="post"' in html
    assert 'action="/login"' in html
    assert 'name="next"' in html
    assert 'value="/dashboard"' in html
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert 'for="email"' in html
    assert 'for="password"' in html
    assert 'aria-label="Principal"' not in html  # login stays outside the authenticated shell


def test_authenticated_shell_has_nav_and_viewport(client):
    c, _ = client
    html = c.get("/").text
    assert 'name="viewport"' in html
    assert 'href="/"' in html
    assert 'href="/dashboard"' in html
    assert 'href="/admin/folio-batches"' in html
    assert 'href="/review"' in html
    assert 'action="/logout"' in html
    assert 'Saltar al contenido' in html
    assert 'aria-label="Principal"' in html


def test_home_new_lote_form_names(client):
    c, session_local = client
    db = session_local()
    try:
        db.add(FolioBatch(label="Semana UI", mode="sequential", prefix="B-", start_number=1, count=1))
        db.commit()
    finally:
        db.close()

    html = c.get("/").text
    assert 'action="/batches"' in html
    assert 'name="label"' in html
    assert 'name="kind"' in html
    assert 'name="producer_id"' in html
    assert 'name="created_by"' in html
    assert 'action="/batches/delete"' in html or "Aún no hay lotes" in html


def test_dashboard_overview_filter_and_export_urls(client):
    c, _ = client
    html = c.get("/dashboard").text
    assert 'action="/dashboard"' in html
    assert 'name="batch_id"' in html
    assert 'name="status"' in html
    assert 'name="fletero"' in html
    assert 'option value="auto_processed"' in html
    assert 'option value="needs_review"' in html
    assert "/api/exports/csv" in html
    assert "/api/exports/json" in html
    assert "Pago a confirmar" in html


def test_review_detail_preserves_field_names_and_actions(client):
    c, session_local = client
    db = session_local()
    try:
        batch = Batch(label="rev-ui")
        db.add(batch)
        db.flush()
        boleta = Boleta(
            batch_id=batch.id,
            original_filename="b.png",
            stored_path="b.png",
            mime_type="image/png",
            page_number=1,
            sha256_hash="ui",
        )
        db.add(boleta)
        db.flush()
        record = BoletaRecord(
            boleta_id=boleta.id,
            folio="B-9001",
            status="needs_review",
            exceptions=["unknown_route", "low_ocr_confidence"],
            confidence_score=0.42,
            ocr_engine="tesseract",
        )
        db.add(record)
        db.commit()
        record_id = record.id
    finally:
        db.close()

    html = c.get(f"/review/{record_id}").text
    assert f'action="/review/{record_id}"' in html
    for name in (
        "folio",
        "date",
        "proveedor",
        "destination",
        "contract_number",
        "fletero",
        "truck_box_number",
        "poder_calorifico_superior",
        "humedad_pct",
        "ceniza_pct",
        "azufre_pct",
        "fsi",
        "granulometria",
        "origin",
        "secondary_origin",
        "concesion_minera",
        "weight_declared",
        "weight",
        "representante_legal",
        "material",
        "trip_type",
        "edited_by",
        "note",
    ):
        assert f'name="{name}"' in html, name
    assert 'name="action" value="correct"' in html
    assert 'name="action" value="approve"' in html
    assert "<fieldset" in html
    assert "Identificación" in html
    assert "Ruta y transporte" in html
    assert "Calidad del carbón" in html
    assert "Información de auditoría" in html
    assert "Boleta escaneada" in html


def test_review_queue_empty_state(client):
    c, _ = client
    html = c.get("/review").text
    assert "No hay boletas pendientes de revisión" in html


def test_css_tokens_file_is_complete():
    css = Path(BASE_DIR / "app" / "web" / "static" / "style.css").read_text()
    for token in (
        "--bg:",
        "--surface:",
        "--text:",
        "--text-muted:",
        "--primary:",
        "--success:",
        "--warning:",
        "--danger:",
        "--info:",
        "--border:",
        "--focus-ring:",
        "--font-sans:",
        "--space-4:",
        "--radius-md:",
        "--shadow-sm:",
        "--content:",
        "--duration:",
        "--ease:",
    ):
        assert token in css, token
