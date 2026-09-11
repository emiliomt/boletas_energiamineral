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


def test_entrada_csv_export_simple_profile(client_and_session):
    client, session_local = client_and_session
    from app.models import Batch, Boleta, BoletaRecord, Producer

    db = session_local()
    try:
        prov = Producer(name="Prov A", active=True)
        db.add(prov)
        db.flush()
        batch = Batch(label="Lote Ingesta", kind="entrada", producer_id=prov.id)
        db.add(batch)
        db.flush()
        batch_id = batch.id
        boleta = Boleta(
            batch_id=batch.id,
            original_filename="scan.jpg",
            stored_path="/tmp/scan.jpg",
            mime_type="image/jpeg",
            page_number=1,
            sha256_hash="abc",
            document_type="boleta",
        )
        db.add(boleta)
        db.flush()
        rec = BoletaRecord(
            boleta_id=boleta.id,
            kind="entrada",
            date="2025-01-02",
            truck_box_number="123",
            fletero="Juan",
            weight=None,
            weight_source="missing",
        )
        db.add(rec)
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/exports/csv?batch_id={batch_id}")
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    assert lines[0] == "transportista,fecha,numero_caja,chofer"
    assert "peso_neto" not in lines[0]
    # Row has four comma-separated values
    assert len(lines[1].split(",")) == 4


def test_entrada_csv_export_con_peso_profile(client_and_session):
    client, session_local = client_and_session
    from app.models import Batch, Boleta, BoletaRecord, Producer, Transportista

    db = session_local()
    try:
        prov = Producer(name="Prov B", active=True)
        db.add(prov)
        t = Transportista(canonical_name="Trans Z", active=True)
        db.add(t)
        db.flush()
        batch = Batch(label="Lote Minsa", kind="entrada", producer_id=prov.id, transportista_id=t.id)
        db.add(batch)
        db.flush()
        batch_id = batch.id
        # Record with measured weight
        b1 = Boleta(
            batch_id=batch.id,
            original_filename="scan1.jpg",
            stored_path="/tmp/scan1.jpg",
            mime_type="image/jpeg",
            page_number=1,
            sha256_hash="abc1",
            document_type="boleta",
        )
        db.add(b1)
        db.flush()
        r1 = BoletaRecord(
            boleta_id=b1.id,
            kind="entrada",
            date="2025-02-03",
            truck_box_number="456",
            fletero="Pedro",
            weight=63240.0,
            weight_source="measured",
        )
        db.add(r1)
        # Record without measured weight
        b2 = Boleta(
            batch_id=batch.id,
            original_filename="scan2.jpg",
            stored_path="/tmp/scan2.jpg",
            mime_type="image/jpeg",
            page_number=1,
            sha256_hash="abc2",
            document_type="boleta",
        )
        db.add(b2)
        db.flush()
        r2 = BoletaRecord(
            boleta_id=b2.id,
            kind="entrada",
            date="2025-02-04",
            truck_box_number="789",
            fletero="Luis",
            weight=None,
            weight_source="missing",
        )
        db.add(r2)
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/exports/csv?batch_id={batch_id}")
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    # Header includes peso_neto for con-peso profile
    assert lines[0] == "transportista,fecha,numero_caja,chofer,peso_neto"
    # First row includes transportista and peso_neto as integer text
    assert lines[1].startswith("Trans Z,2025-02-03,456,Pedro,63240")
    # Second row leaves peso_neto blank
    assert lines[2].endswith(",")

