"""Admin dashboard: build_overview aggregation + the /dashboard page render."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import Batch, Boleta, BoletaRecord
from app.reporting.summary import build_overview


def _add_record(db, batch, folio, status, fletero=None, tariff=None, material=None, qty=None):
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=f"{folio}.png",
        stored_path=f"{folio}.png",
        mime_type="image/png",
        page_number=1,
        sha256_hash=folio,
    )
    db.add(boleta)
    db.flush()
    rec = BoletaRecord(
        boleta_id=boleta.id,
        folio=folio,
        status=status,
        fletero=fletero,
        tariff_amount=tariff,
        material=material,
        inventory_quantity=qty,
        origin="Mina San Jose",
        destination="Planta Norte",
    )
    db.add(rec)
    db.flush()
    return rec


def test_build_overview_aggregates_only_auto_processed(db_session):
    batch = Batch(label="Lote 1")
    db_session.add(batch)
    db_session.flush()
    _add_record(db_session, batch, "A-1", "auto_processed", fletero="Juan", tariff=850.0, material="carbon", qty=-9000.0)
    _add_record(db_session, batch, "A-2", "auto_processed", fletero="Juan", tariff=850.0, material="carbon", qty=-8000.0)
    _add_record(db_session, batch, "A-3", "needs_review", fletero="Maria", tariff=999.0, material="carbon", qty=-1.0)

    ov = build_overview(db_session)

    assert ov.total_boletas == 3
    assert ov.auto_processed_count == 2
    assert ov.needs_review_count == 1
    # Only the two auto_processed count toward money/inventory.
    assert ov.total_payable == 1700.0
    assert ov.total_payment_by_fletero == {"Juan": 1700.0}
    assert ov.net_inventory_by_material == {"carbon": -17000.0}
    assert "Maria" not in ov.total_payment_by_fletero


def test_build_overview_filters(db_session):
    b1 = Batch(label="L1")
    b2 = Batch(label="L2")
    db_session.add_all([b1, b2])
    db_session.flush()
    _add_record(db_session, b1, "A-1", "auto_processed", fletero="Juan", tariff=850.0)
    _add_record(db_session, b2, "B-1", "needs_review", fletero="Maria", tariff=100.0)

    assert build_overview(db_session, batch_id=b1.id).total_boletas == 1
    assert build_overview(db_session, status="needs_review").total_boletas == 1
    assert build_overview(db_session, fletero="Juan").rows[0].record.folio == "A-1"


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


def test_dashboard_page_renders_boletas(client):
    c, session_local = client
    db = session_local()
    try:
        batch = Batch(label="Semana 45")
        db.add(batch)
        db.flush()
        _add_record(db, batch, "B-6001", "auto_processed", fletero="Luis Perez", tariff=850.0, material="carbon", qty=-900.0)
        db.commit()
    finally:
        db.close()

    resp = c.get("/dashboard")
    assert resp.status_code == 200
    body = resp.text
    assert "Tablero de Boletas" in body
    assert "B-6001" in body
    assert "Luis Perez" in body
    assert "Pago a confirmar" in body


def test_dashboard_empty_filter_params_do_not_error(client):
    # The filter form submits empty selects as ?batch_id=&status=&fletero=;
    # that must not 422 (regression: batch_id used to be parsed as int).
    c, _ = client
    resp = c.get("/dashboard", params={"batch_id": "", "status": "", "fletero": ""})
    assert resp.status_code == 200


def test_dashboard_status_filter_via_query(client):
    c, session_local = client
    db = session_local()
    try:
        batch = Batch(label="L")
        db.add(batch)
        db.flush()
        _add_record(db, batch, "OK-1", "auto_processed", fletero="Juan", tariff=850.0)
        _add_record(db, batch, "REV-1", "needs_review", fletero="Maria")
        db.commit()
    finally:
        db.close()

    resp = c.get("/dashboard", params={"status": "needs_review"})
    assert resp.status_code == 200
    assert "REV-1" in resp.text
    assert "OK-1" not in resp.text
