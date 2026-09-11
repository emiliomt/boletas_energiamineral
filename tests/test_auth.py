"""Auth: unauthenticated requests are blocked (401 for API, redirect for
web), authenticated ones pass. Clerk is used in production; tests avoid
real Clerk by relying on FastAPI dependency overrides elsewhere."""
from __future__ import annotations

from fastapi.testclient import TestClient


def client(tmp_path, monkeypatch):
    import app.config as app_config
    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.main import app

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))

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

