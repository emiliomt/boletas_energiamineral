"""Admin page /admin/proveedores: add suppliers and their caja/transporte prices."""
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


def _seed_folio_batch(session_local) -> None:
    from app.models import FolioBatch

    db = session_local()
    try:
        db.add(FolioBatch(label="Sem 30", mode="sequential", prefix="B-", start_number=1, count=1))
        db.commit()
    finally:
        db.close()


def test_proveedores_page_is_in_nav_and_lists_csv_producers(client_and_session):
    client, _ = client_and_session

    html = client.get("/admin/proveedores").text

    assert 'href="/admin/proveedores"' in client.get("/").text
    assert "<h1>Proveedores</h1>" in html
    assert "Precio por caja de carbón" in html
    assert "Precio de transporte" in html
    assert "Bradfort" in html
    assert "CTU/MINSA" in html


def test_create_proveedor_with_prices(client_and_session):
    client, session_local = client_and_session
    _seed_folio_batch(session_local)

    resp = client.post(
        "/admin/proveedores",
        data={
            "name": "Mina El Roble",
            "default_origin": "El Roble",
            "precio_caja_carbon": "1250.50",
            "precio_transporte": "800",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/proveedores"

    from app.models import Producer

    db = session_local()
    try:
        row = db.query(Producer).filter_by(name="Mina El Roble").one()
        assert row.default_origin == "El Roble"
        assert row.precio_caja_carbon == 1250.50
        assert row.precio_transporte == 800.0
        assert row.active is True
    finally:
        db.close()

    html = client.get("/admin/proveedores").text
    assert "Mina El Roble" in html
    assert "1250.50" in html
    assert "800.00" in html

    dashboard = client.get("/").text
    assert "Mina El Roble" in dashboard


def test_create_proveedor_rejects_duplicate_name(client_and_session):
    client, _ = client_and_session

    html = client.post(
        "/admin/proveedores",
        data={"name": "Bradfort", "precio_caja_carbon": "1", "precio_transporte": "1"},
    ).text

    assert "Ya existe un proveedor" in html


def test_update_proveedor_prices(client_and_session):
    client, session_local = client_and_session

    from app.models import Producer

    db = session_local()
    try:
        producer = db.query(Producer).filter_by(name="Bradfort").one()
        producer_id = producer.id
    finally:
        db.close()

    resp = client.post(
        f"/admin/proveedores/{producer_id}",
        data={
            "default_origin": "Bradfort Norte",
            "precio_caja_carbon": "2000",
            "precio_transporte": "950.25",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db = session_local()
    try:
        producer = db.get(Producer, producer_id)
        assert producer.default_origin == "Bradfort Norte"
        assert producer.precio_caja_carbon == 2000.0
        assert producer.precio_transporte == 950.25
    finally:
        db.close()


def test_toggle_deactivates_and_hides_from_entrada_dropdown(client_and_session):
    client, session_local = client_and_session
    _seed_folio_batch(session_local)

    from app.models import Producer

    db = session_local()
    try:
        producer = db.query(Producer).filter_by(name="Bradfort").one()
        producer_id = producer.id
        assert producer.active is True
    finally:
        db.close()

    client.post(f"/admin/proveedores/{producer_id}/toggle", follow_redirects=False)

    db = session_local()
    try:
        assert db.get(Producer, producer_id).active is False
    finally:
        db.close()

    dashboard = client.get("/").text
    assert f'<option value="{producer_id}">Bradfort</option>' not in dashboard


def test_csv_reload_preserves_ui_prices(client_and_session):
    client, session_local = client_and_session

    from app.models import Producer
    from app.rules.config_loader import load_producers

    db = session_local()
    try:
        producer = db.query(Producer).filter_by(name="Bradfort").one()
        producer.precio_caja_carbon = 111.0
        producer.precio_transporte = 222.0
        db.commit()
        load_producers(db)
        producer = db.query(Producer).filter_by(name="Bradfort").one()
        assert producer.precio_caja_carbon == 111.0
        assert producer.precio_transporte == 222.0
    finally:
        db.close()
