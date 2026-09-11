from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.config as app_config
import app.db as app_db
from app.engines.folio_registry import check_folio
from app.engines.salida_reconciliation import find_salida_counterpart
from app.ingestion.storage import store_upload
from app.models import Batch, Boleta, BoletaRecord, Folio, FolioBatch


def _setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", SessionLocal)
    return engine, SessionLocal


def test_session_secret_fail_closed(tmp_path, monkeypatch):
    # Configure prod-like environment and insecure secret; importing app.main must raise
    monkeypatch.setattr(app_config.settings, "environment", "production")
    monkeypatch.setattr(app_config.settings, "session_secret_key", "dev-only-insecure-secret-change-me")
    # Use a temp DB so init_db can run if it ever gets that far (it shouldn't)
    _setup_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        importlib.reload(importlib.import_module("app.main"))
    # Restore dev env for other tests
    monkeypatch.setattr(app_config.settings, "environment", "development")
    monkeypatch.setattr(app_config.settings, "session_secret_key", "unit-test-secret")
    importlib.reload(importlib.import_module("app.main"))


def test_upload_filename_sanitize_and_limit(tmp_path, monkeypatch):
    engine, SessionLocal = _setup_db(tmp_path, monkeypatch)
    from app.db import Base, init_db

    Base.metadata.create_all(bind=engine)
    init_db()
    db = SessionLocal()
    try:
        batch = Batch(label="u1")
        db.add(batch)
        db.commit()
        db.refresh(batch)

        # Reject path traversal / absolute names
        with pytest.raises(ValueError):
            store_upload(db, batch, "../../evil.pdf", b"%PDF-1.4", "application/pdf", "boleta")
        with pytest.raises(ValueError):
            store_upload(db, batch, "/abs/path.jpg", b"x", "image/jpeg", "boleta")

        # Accept safe name; stored_path must be content-hash-based prefix, not raw filename
        created = store_upload(db, batch, "photo.JPG", b"\x89PNGdata", "image/png", "boleta")
        assert created
        assert created[0].original_filename == "photo.JPG"
        assert Path(created[0].stored_path).name.startswith(created[0].sha256_hash[:12] + "_")
    finally:
        db.close()


def test_login_next_is_clamped_to_in_site_path(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from app.main import app
    with TestClient(app) as c:
        html = c.get("/login?next=https://evil.com").text
        # Clerk sign-in container should carry a safe relative next path
        if 'id="clerk-signin"' in html:
            assert 'data-next="/"' in html
        else:
            assert "Clerk no está configurado" in html


def test_folio_void_rejected_and_salida_counterpart_batch_scoped(tmp_path, monkeypatch):
    engine, SessionLocal = _setup_db(tmp_path, monkeypatch)
    from app.db import Base, init_db

    Base.metadata.create_all(bind=engine)
    init_db()
    db = SessionLocal()
    try:
        # Setup two batches and folios
        b1 = Batch(label="s1")
        b2 = Batch(label="s2")
        db.add_all([b1, b2])
        db.flush()
        # Folio table row voided
        fb = FolioBatch(label="F", mode="sequential", prefix="X", start_number=1, count=1)
        db.add(fb)
        db.flush()
        f = Folio(folio_batch_id=fb.id, folio="F-100", qr_payload="X", status="void")
        db.add(f)
        db.flush()
        # Check folio validation flags void
        result = check_folio(db, "F-100", None)
        assert result.status == "void"
        assert "folio_void" in result.exceptions

        # Create a Salida partial only in a different batch with same folio
        boleta2 = Boleta(batch_id=b2.id, original_filename="b.png", stored_path="b.png", mime_type="image/png", page_number=1, sha256_hash="h2")
        db.add(boleta2)
        db.flush()
        r2 = BoletaRecord(boleta_id=boleta2.id, kind="salida", folio="S-9", salida_status="cfe_slip_only")
        db.add(r2)
        db.commit()
        # Finding counterpart for batch 1 must not match across batches
        match = find_salida_counterpart(db, batch_id=b1.id, folio="S-9", document_type="cfe_slip")
        assert match.salida_status != "complete"
    finally:
        db.close()

