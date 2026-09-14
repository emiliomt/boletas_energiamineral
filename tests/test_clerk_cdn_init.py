from __future__ import annotations

import base64
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_login_includes_clerk_cdn_and_data_key(tmp_path, monkeypatch):
    # Configure a temp SQLite DB and storage paths
    import app.config as app_config
    import app.db as app_db

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", SessionLocal)

    # Publishable key encoding 'clerk.accounts.dev' in base64url after pk_test_
    host_b64url = base64.urlsafe_b64encode(b"clerk.accounts.dev").decode("ascii").rstrip("=")
    pk = f"pk_test_{host_b64url}"
    monkeypatch.setattr(app_config.settings, "clerk_publishable_key", pk)

    from app.main import app

    with TestClient(app) as c:
        html = c.get("/login").text
        assert f'data-clerk-publishable-key="{pk}"' in html
        assert 'src="https://clerk.accounts.dev/npm/@clerk/ui@1/dist/ui.browser.js"' in html
        assert 'src="https://clerk.accounts.dev/npm/@clerk/clerk-js@6/dist/clerk.browser.js"' in html
        # Old v5 path should not be present
        assert "@clerk/clerk-js@5" not in html

