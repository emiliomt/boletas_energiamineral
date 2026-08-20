"""Selectable deletion of folio batches (folios) and scanning batches (proyectos)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import Batch, Boleta, BoletaRecord, Folio, FolioBatch


@pytest.fixture()
def client_and_session(tmp_path, monkeypatch):
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

    app.dependency_overrides[require_admin_api] = lambda: "a@example.com"
    app.dependency_overrides[require_admin_web] = lambda: "a@example.com"
    try:
        with TestClient(app) as c:
            yield c, session_local
    finally:
        app.dependency_overrides.clear()


def test_delete_selected_folio_batches_removes_them_and_their_folios(client_and_session):
    client, session_local = client_and_session
    db = session_local()
    try:
        keep = FolioBatch(label="keep", mode="imported", count=1)
        drop = FolioBatch(label="drop", mode="imported", count=2)
        db.add_all([keep, drop])
        db.flush()
        db.add(Folio(folio_batch_id=keep.id, folio="K-1", qr_payload="BOL:K-1"))
        db.add(Folio(folio_batch_id=drop.id, folio="D-1", qr_payload="BOL:D-1"))
        db.add(Folio(folio_batch_id=drop.id, folio="D-2", qr_payload="BOL:D-2"))
        db.commit()
        drop_id, keep_id = drop.id, keep.id
    finally:
        db.close()

    resp = client.post("/admin/folio-batches/delete", data={"ids": [drop_id]}, follow_redirects=False)
    assert resp.status_code == 303

    db = session_local()
    try:
        assert db.get(FolioBatch, drop_id) is None
        assert db.get(FolioBatch, keep_id) is not None
        assert db.query(Folio).filter_by(folio="D-1").count() == 0  # folios cascade-deleted
        assert db.query(Folio).filter_by(folio="D-2").count() == 0
        assert db.query(Folio).filter_by(folio="K-1").count() == 1
    finally:
        db.close()


def test_delete_selected_batches_cascades_and_unlinks_folios(client_and_session):
    client, session_local = client_and_session
    db = session_local()
    try:
        # A scanning batch with one boleta+record, and an issued folio linked to it.
        batch = Batch(label="proyecto")
        db.add(batch)
        db.flush()
        boleta = Boleta(batch_id=batch.id, original_filename="b.png", stored_path="b.png",
                        mime_type="image/png", page_number=1, sha256_hash="h")
        db.add(boleta)
        db.flush()
        record = BoletaRecord(boleta_id=boleta.id, folio="F-1", status="needs_review")
        db.add(record)
        db.flush()
        fb = FolioBatch(label="fb", mode="imported", count=1)
        db.add(fb)
        db.flush()
        folio = Folio(folio_batch_id=fb.id, folio="F-1", qr_payload="BOL:F-1", status="scanned", boleta_record_id=record.id)
        db.add(folio)
        db.commit()
        batch_id, record_id, folio_id = batch.id, record.id, folio.id
    finally:
        db.close()

    resp = client.post("/batches/delete", data={"ids": [batch_id]}, follow_redirects=False)
    assert resp.status_code == 303

    db = session_local()
    try:
        assert db.get(Batch, batch_id) is None
        assert db.query(Boleta).filter_by(batch_id=batch_id).count() == 0
        assert db.get(BoletaRecord, record_id) is None  # cascaded
        folio = db.get(Folio, folio_id)
        assert folio is not None  # the folio itself survives (it belongs to a folio batch)
        assert folio.status == "issued"  # ...but it was unlinked back to issued
        assert folio.boleta_record_id is None
    finally:
        db.close()


def test_delete_with_no_selection_is_a_noop(client_and_session):
    client, session_local = client_and_session
    db = session_local()
    try:
        db.add(Batch(label="x"))
        db.commit()
    finally:
        db.close()

    # No ids posted (nothing selected) -> should redirect without deleting.
    resp = client.post("/batches/delete", data={}, follow_redirects=False)
    assert resp.status_code == 303

    db = session_local()
    try:
        assert db.query(Batch).count() == 1
    finally:
        db.close()
