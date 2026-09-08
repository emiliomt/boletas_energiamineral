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


def test_creating_entrada_requires_proveedor_selection(client):
    c, session_local = client
    # Seed a transportista to satisfy new required field
    from app.models import Transportista
    db = session_local()
    try:
        t = Transportista(canonical_name="Fletero Req", active=True)
        db.add(t)
        db.commit()
        transportista_id = t.id
    finally:
        db.close()
    # Missing producer_id for kind=entrada should set a flash error and redirect back
    resp = c.post(
        "/batches",
        data={
            "label": "Semana Test",
            "kind": "entrada",
            "producer_id": "",
            "transportista_id": str(transportista_id),
            "created_by": "tester",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    html = resp.text
    assert "Para lotes de Entrada, selecciona un proveedor." in html


def test_creating_salida_does_not_require_proveedor(client):
    c, session_local = client
    # Seed a transportista to satisfy new required field
    from app.models import Transportista
    db = session_local()
    try:
        t = Transportista(canonical_name="Fletero Req", active=True)
        db.add(t)
        db.commit()
        transportista_id = t.id
    finally:
        db.close()
    resp = c.post(
        "/batches",
        data={
            "label": "Semana Salida",
            "kind": "salida",
            "producer_id": "",
            "transportista_id": str(transportista_id),
            "created_by": "tester",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)
    # Verify batch was created with kind=salida and no producer_id
    from app.models import Batch

    db = session_local()
    try:
        batch = db.query(Batch).order_by(Batch.id.desc()).first()
        assert batch is not None
        assert batch.kind == "salida"
        assert batch.producer_id is None
    finally:
        db.close()

