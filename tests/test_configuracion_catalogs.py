from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR
from app.models import Batch, Boleta, BoletaRecord, Proveedor, Transportista


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    # Ensure static files exist
    (BASE_DIR / "app" / "web" / "static").mkdir(parents=True, exist_ok=True)

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


def test_proveedores_crud(client):
    c, session_local = client
    # List page renders
    assert c.get("/admin/config/proveedores").status_code == 200
    # Create
    resp = c.post(
        "/admin/config/proveedores",
        data={"name": "Proveedor Uno", "origin": "Norte", "precio_caja": "100.5", "precio_transporte": "250.0", "active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)
    html = c.get("/admin/config/proveedores").text
    assert "Proveedor Uno" in html
    assert "Norte" in html
    assert "100.5" in html
    assert "250.0" in html
    # Update and deactivate
    db = session_local()
    try:
        p = db.query(Proveedor).filter_by(name="Proveedor Uno").one()
        pid = p.id
    finally:
        db.close()
    resp = c.post(
        "/admin/config/proveedores",
        data={"proveedor_id": str(pid), "name": "Proveedor Uno", "origin": "Sur", "precio_caja": "111", "precio_transporte": "", "active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)
    html = c.get("/admin/config/proveedores").text
    assert "Sur" in html
    assert "111" in html
    resp = c.post(f"/admin/config/proveedores/{pid}/toggle", follow_redirects=False)
    assert resp.status_code in (303, 302)
    html = c.get("/admin/config/proveedores").text
    assert "Inactivo" in html


def test_transportistas_crud(client):
    c, session_local = client
    # List page renders
    assert c.get("/admin/config/transportistas").status_code == 200
    # Create
    resp = c.post(
        "/admin/config/transportistas",
        data={"canonical_name": "Fletero Uno", "phone": "555-1234", "notes": "Camión rojo", "active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)
    html = c.get("/admin/config/transportistas").text
    assert "Fletero Uno" in html
    assert "555-1234" in html
    assert "Camión rojo" in html
    # Update and deactivate
    db = session_local()
    try:
        t = db.query(Transportista).filter_by(canonical_name="Fletero Uno").one()
        tid = t.id
    finally:
        db.close()
    resp = c.post(
        "/admin/config/transportistas",
        data={"transportista_id": str(tid), "canonical_name": "Fletero Uno", "phone": "", "notes": "Actualizado", "active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 302)
    html = c.get("/admin/config/transportistas").text
    assert "Actualizado" in html
    resp = c.post(f"/admin/config/transportistas/{tid}/toggle", follow_redirects=False)
    assert resp.status_code in (303, 302)
    html = c.get("/admin/config/transportistas").text
    assert "Inactivo" in html


def test_datalists_exist_and_are_populated(client):
    c, session_local = client
    # Seed one proveedor and one transportista
    c.post("/admin/config/proveedores", data={"name": "Prov Sugerido", "origin": "", "precio_caja": "", "precio_transporte": "", "active": "1"})
    c.post("/admin/config/transportistas", data={"canonical_name": "Fletero Sugerido", "phone": "", "notes": "", "active": "1"})
    # Create a minimal record to open the review page
    db = session_local()
    try:
        batch = Batch(label="cfg-ui")
        db.add(batch)
        db.flush()
        boleta = Boleta(
            batch_id=batch.id,
            original_filename="b.png",
            stored_path=str(Path(BASE_DIR / "tests" / "fixtures" / "boletas" / "sample_boleta_01.png")),
            mime_type="image/png",
            page_number=1,
            sha256_hash="cfg",
        )
        db.add(boleta)
        db.flush()
        record = BoletaRecord(boleta_id=boleta.id, status="needs_review")
        db.add(record)
        db.commit()
        record_id = record.id
    finally:
        db.close()
    html = c.get(f"/review/{record_id}").text
    assert 'datalist id="proveedores-list"' in html
    assert 'datalist id="transportistas-list"' in html
    assert 'option value="Prov Sugerido"' in html
    assert 'option value="Fletero Sugerido"' in html
    # Folio batch form also includes proveedores datalist
    html2 = c.get("/admin/folio-batches").text
    assert 'datalist id="proveedores-list"' in html2
    assert 'option value="Prov Sugerido"' in html2

