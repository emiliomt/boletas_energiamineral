"""The dashboard "Nuevo lote" name field is a dropdown of the registered
folio batches (Lotes de Folios), not a free-text input."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_and_session(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(app_db, "engine", test_engine)
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


def _seed_folio_batch(session_local, label: str) -> None:
    from app.models import FolioBatch

    db = session_local()
    try:
        db.add(FolioBatch(label=label, mode="sequential", prefix="B-", start_number=1, count=1))
        db.commit()
    finally:
        db.close()


def test_dashboard_shows_registered_lotes_as_dropdown(client_and_session):
    client, session_local = client_and_session
    _seed_folio_batch(session_local, "Semana 33")
    _seed_folio_batch(session_local, "Semana 34")

    html = client.get("/").text

    assert '<select name="label"' in html
    assert '<option value="Semana 33">Semana 33</option>' in html
    assert '<option value="Semana 34">Semana 34</option>' in html
    # the old free-text input must be gone
    assert 'name="label" placeholder=' not in html


def test_dashboard_empty_state_when_no_registered_lotes(client_and_session):
    client, _ = client_and_session

    html = client.get("/").text

    assert '<select name="label"' not in html
    assert "No hay lotes registrados" in html
    assert "/admin/folio-batches" in html


def test_selected_lote_name_creates_batch_with_that_label(client_and_session):
    client, session_local = client_and_session
    _seed_folio_batch(session_local, "Semana 33")

    resp = client.post("/batches", data={"label": "Semana 33", "created_by": "tester"}, follow_redirects=False)
    assert resp.status_code == 303

    from app.models import Batch

    db = session_local()
    try:
        batch = db.query(Batch).order_by(Batch.id.desc()).first()
        assert batch is not None
        assert batch.label == "Semana 33"
        assert batch.created_by == "tester"
    finally:
        db.close()
