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


def _seed_transportistas(session_local):
    from app.models import Transportista

    db = session_local()
    try:
        active = Transportista(canonical_name="Fletero Activo", active=True)
        inactive = Transportista(canonical_name="Fletero Inactivo", active=False)
        db.add_all([active, inactive])
        db.commit()
        return active.id, inactive.id
    finally:
        db.close()


def _seed_folio_batch(session_local, label: str) -> None:
    from app.models import FolioBatch

    db = session_local()
    try:
        db.add(FolioBatch(label=label, mode="sequential", prefix="B-", start_number=1, count=1))
        db.commit()
    finally:
        db.close()


def test_dashboard_shows_active_transportistas_in_dropdown(client):
    c, session_local = client
    _seed_transportistas(session_local)
    _seed_folio_batch(session_local, "Semana 40")

    html = c.get("/").text
    assert 'id="batch-transportista"' in html
    assert ">Fletero Activo<" in html
    assert "Fletero Inactivo" not in html


def test_creating_batches_require_transportista_for_both_kinds(client):
    c, session_local = client
    active_id, _ = _seed_transportistas(session_local)
    _seed_folio_batch(session_local, "Semana 41")

    # Salida with transportista succeeds
    resp = c.post(
        "/batches",
        data={"label": "Semana 41", "kind": "salida", "transportista_id": str(active_id), "created_by": "tester"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)

    # Entrada also requires proveedor; omit it to trigger proveedor error but include transportista
    resp2 = c.post(
        "/batches",
        data={"label": "Semana 41 E", "kind": "entrada", "producer_id": "", "transportista_id": str(active_id)},
        follow_redirects=True,
    )
    assert resp2.status_code == 200
    assert "Para lotes de Entrada, selecciona un proveedor." in resp2.text
