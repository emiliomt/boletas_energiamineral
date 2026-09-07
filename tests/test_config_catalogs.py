from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import Batch, Boleta, BoletaRecord, Proveedor, Transportista


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
    app_db.Base.metadata.create_all(bind=engine)
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


def _mk_record(session_local):
    db = session_local()
    try:
        batch = Batch(label="cfg-ui")
        db.add(batch)
        db.flush()
        boleta = Boleta(
            batch_id=batch.id,
            original_filename="b.png",
            stored_path="b.png",
            mime_type="image/png",
            page_number=1,
            sha256_hash="cfg",
        )
        db.add(boleta)
        db.flush()
        record = BoletaRecord(
            boleta_id=boleta.id,
            folio="B-1000",
            status="needs_review",
            confidence_score=0.5,
        )
        db.add(record)
        db.commit()
        return record.id
    finally:
        db.close()


def test_nav_has_config_link(client):
    c, _ = client
    html = c.get("/").text
    assert 'href="/admin/config"' in html


def test_config_home_links(client):
    c, _ = client
    html = c.get("/admin/config").text
    assert "Configuración" in html
    assert '/admin/config/proveedores' in html
    assert '/admin/config/transportistas' in html


def test_proveedores_crud_and_datalists(client):
    c, session_local = client
    # Create
    resp = c.post(
        "/admin/config/proveedores",
        data={"name": "Carbones Sabinas", "origin": "Sabinas", "precio_caja": "123.45", "precio_transporte": "456.78"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 307)
    # List shows
    html = c.get("/admin/config/proveedores").text
    assert "Carbones Sabinas" in html
    assert "Sabinas" in html
    # Update (deactivate)
    db = session_local()
    try:
        prov = db.query(Proveedor).filter_by(name="Carbones Sabinas").one()
        resp = c.post(
            f"/admin/config/proveedores/{prov.id}/update",
            data={"name": "Carbones Sabinas", "origin": "Nueva Rosita", "precio_caja": "200", "precio_transporte": "300", "active": ""},
            follow_redirects=False,
        )
        assert resp.status_code in (303, 307)
        db.refresh(prov)
        assert prov.origin == "Nueva Rosita"
        assert prov.precio_caja == 200.0
        assert prov.precio_transporte == 300.0
        assert prov.active is False
    finally:
        db.close()
    # Datalist appears in folio-batches and review forms
    html_batches = c.get("/admin/folio-batches").text
    assert 'datalist id="proveedor-list"' in html_batches
    assert '<option value="Carbones Sabinas">' in html_batches


def test_transportistas_crud_and_review_datalist(client):
    c, session_local = client
    # Create
    resp = c.post(
        "/admin/config/transportistas",
        data={"canonical_name": "Juan Pérez", "phone": "555-1234", "notes": "Camión rojo"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 307)
    # List shows
    html = c.get("/admin/config/transportistas").text
    assert "Juan Pérez" in html
    assert "555-1234" in html
    assert "Camión rojo" in html
    # Deactivate
    db = session_local()
    try:
        t = db.query(Transportista).filter_by(canonical_name="Juan Pérez").one()
        resp = c.post(f"/admin/config/transportistas/{t.id}/toggle", follow_redirects=False)
        assert resp.status_code in (303, 307)
        db.refresh(t)
        assert t.active is False
    finally:
        db.close()
    # Review page has datalist
    rec_id = _mk_record(session_local)
    html = c.get(f"/review/{rec_id}").text
    assert 'datalist id="fletero-list"' in html
    assert '<option value="Juan Pérez">' in html
