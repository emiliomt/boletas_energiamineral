"""Auth: unauthenticated requests are blocked (401 for API, redirect for
web), authenticated ones pass, and verify_credentials talks to Supabase's
REST API correctly (mocked -- no real Supabase in tests)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.supabase_auth import SupabaseNotConfigured, verify_credentials


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health_is_open(client):
    assert client.get("/api/health").status_code == 200


def test_login_page_is_open(client):
    assert client.get("/login").status_code == 200


def test_unauthenticated_api_request_returns_401(client):
    resp = client.post("/api/batches", json={"label": "x"})
    assert resp.status_code == 401


def test_unauthenticated_web_request_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_folio_batches_api_also_requires_auth(client):
    resp = client.get("/api/folio-batches")
    assert resp.status_code == 401


def test_proveedores_page_requires_auth(client):
    resp = client.get("/admin/proveedores", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_verify_credentials_success(monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(app_config.settings, "supabase_anon_key", "fake-anon-key")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"access_token": "fake-token", "user": {"email": "admin@example.com"}}

    def fake_post(url, **kwargs):
        assert url == "https://example.supabase.co/auth/v1/token"
        assert kwargs["params"] == {"grant_type": "password"}
        return FakeResponse()

    import app.auth.supabase_auth as supabase_auth_module

    monkeypatch.setattr(supabase_auth_module.httpx, "post", fake_post)

    result = verify_credentials("admin@example.com", "correct-password")

    assert result["user"]["email"] == "admin@example.com"


def test_verify_credentials_invalid_password_returns_none(monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(app_config.settings, "supabase_anon_key", "fake-anon-key")

    class FakeResponse:
        status_code = 400

        def json(self):
            return {"error": "invalid_grant"}

    import app.auth.supabase_auth as supabase_auth_module

    monkeypatch.setattr(supabase_auth_module.httpx, "post", lambda url, **kwargs: FakeResponse())

    assert verify_credentials("admin@example.com", "wrong-password") is None


def test_verify_credentials_raises_when_not_configured(monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "supabase_url", None)
    monkeypatch.setattr(app_config.settings, "supabase_anon_key", None)

    with pytest.raises(SupabaseNotConfigured):
        verify_credentials("admin@example.com", "any-password")
