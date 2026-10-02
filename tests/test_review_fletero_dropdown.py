"""Review form: Fletero is a dropdown of active transportistas, not free text."""
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


def _review_record(session_local, *, fletero: str | None = None) -> int:
    from app.models import Batch, Boleta, BoletaRecord

    db = session_local()
    try:
        batch = Batch(label="rev-fletero")
        db.add(batch)
        db.flush()
        boleta = Boleta(
            batch_id=batch.id,
            original_filename="b.png",
            stored_path="b.png",
            mime_type="image/png",
            page_number=1,
            sha256_hash=f"fletero-{fletero or 'none'}",
        )
        db.add(boleta)
        db.flush()
        record = BoletaRecord(
            boleta_id=boleta.id,
            status="needs_review",
            destination="C.T. JOSE LOPEZ PORTILLO",
            fletero=fletero,
            truck_box_number="1805",
        )
        db.add(record)
        db.commit()
        return record.id
    finally:
        db.close()


def test_review_fletero_is_dropdown_of_active_transportistas(client):
    from app.models import Transportista

    c, session_local = client
    db = session_local()
    try:
        db.add(Transportista(canonical_name="Fletero Visible", active=True))
        db.add(Transportista(canonical_name="Fletero Oculto", active=False))
        db.commit()
    finally:
        db.close()

    record_id = _review_record(session_local)
    html = c.get(f"/review/{record_id}").text

    assert '<select name="fletero" id="review-fletero">' in html
    assert 'type="text" name="fletero"' not in html
    assert 'option value="Fletero Visible"' in html
    assert ">Fletero Visible<" in html
    assert "Fletero Oculto" not in html
    # Roster loaded from transportista_roster.csv, not a hardcoded template list.
    assert 'option value="CAMAGO"' in html
    assert 'option value="ABREGO O TRANSPORTES MAU"' in html
    assert "Selecciona un fletero…" in html
    assert 'name="action" value="correct"' in html
    assert 'name="truck_box_number"' in html


def test_review_fletero_preselects_catalog_match_including_alias(client):
    c, session_local = client
    record_id = _review_record(session_local, fletero="TRANSPORTES MAU")
    html = c.get(f"/review/{record_id}").text
    assert 'option value="ABREGO O TRANSPORTES MAU" selected' in html


def test_review_fletero_keeps_unmatched_ocr_value_selected(client):
    c, session_local = client
    record_id = _review_record(session_local, fletero="Chofer Desconocido XYZ")
    html = c.get(f"/review/{record_id}").text
    assert 'option value="Chofer Desconocido XYZ" selected' in html
    assert 'option value="CAMAGO"' in html


def test_review_fletero_empty_catalog_shows_spanish_empty_state(client):
    from app.models import Transportista, TransportistaAlias

    c, session_local = client
    record_id = _review_record(session_local, fletero=None)
    db = session_local()
    try:
        db.query(TransportistaAlias).delete()
        db.query(Transportista).delete()
        db.commit()
    finally:
        db.close()

    html = c.get(f"/review/{record_id}").text
    assert "No hay fleteros registrados." in html
    assert 'href="/admin/config/transportistas"' in html
    assert 'id="fletero-vacio"' in html
    assert '<select name="fletero"' in html
    assert 'name="destination"' in html
    assert 'name="action" value="approve"' in html
    assert "CAMAGO" not in html

    resp = c.post(
        f"/review/{record_id}",
        data={
            "action": "correct",
            "fletero": "",
            "destination": "C.T. JOSE LOPEZ PORTILLO",
            "truck_box_number": "1805",
            "edited_by": "tester",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def test_review_fletero_selection_is_saved(client):
    from app.models import BoletaRecord

    c, session_local = client
    record_id = _review_record(session_local, fletero=None)
    resp = c.post(
        f"/review/{record_id}",
        data={
            "action": "correct",
            "fletero": "CAMAGO",
            "destination": "C.T. JOSE LOPEZ PORTILLO",
            "truck_box_number": "1805",
            "edited_by": "tester",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    db = session_local()
    try:
        record = db.get(BoletaRecord, record_id)
        assert record.fletero == "CAMAGO"
        assert record.destination == "C.T. JOSE LOPEZ PORTILLO"
        assert record.truck_box_number == "1805"
    finally:
        db.close()
