"""Auth: unauthenticated requests are blocked (401 for API, redirect for
web), authenticated ones pass. Clerk is used in production; tests avoid
real Clerk by relying on FastAPI dependency overrides elsewhere."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest


@pytest.fixture()
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


_BROWSER = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}


def test_unauthenticated_browser_get_redirects_to_login_html(client):
    """Opening the app in a browser must be the login page, not JSON."""
    resp = client.get("/", headers=_BROWSER, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login?next=/")
    assert "denied=1" not in resp.headers["location"]
    assert "application/json" not in (resp.headers.get("content-type") or "")
    assert resp.text.strip() != '{"detail":"No autorizado"}'
    page = client.get("/", headers=_BROWSER, follow_redirects=True)
    assert page.status_code == 200
    assert "Iniciar sesión" in page.text
    assert "Acceso al registro de boletas." in page.text
    assert '"detail"' not in page.text


def _signed_in_state(email: str | None):
    class State:
        is_signed_in = True
        is_authenticated = True
        payload = {"sub": "user_abc123"}
        reason = None

    if email:
        State.payload = {"sub": "user_abc123", "email": email}
    return State()


def _clerk_client(tmp_path, monkeypatch):
    import app.config as app_config
    import app.db as app_db
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.main import app

    db_path = tmp_path / "allow.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    monkeypatch.setattr(app_config.settings, "clerk_secret_key", "sk_test_unit")
    monkeypatch.setattr(app_config.settings, "clerk_publishable_key", "pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXY")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False))
    return TestClient(app)


def test_browser_allowlist_miss_redirects_to_login_not_json(tmp_path, monkeypatch):
    """The page Emilio saw: a verified session rendered as JSON 403."""
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "admin_emails", "admin@example.com")
    monkeypatch.setattr(
        "app.auth.session.authenticate_request",
        lambda request, options: _signed_in_state(None),
    )
    monkeypatch.setattr("app.auth.session._lookup_clerk_email", lambda user_id: None)

    with _clerk_client(tmp_path, monkeypatch) as client:
        resp = client.get(
            "/",
            headers=_BROWSER,
            cookies={"__session": "session-jwt"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/login?next=/")
        assert "denied=1" in resp.headers["location"]
        assert "No autorizado" not in resp.text
        page = client.get(resp.headers["location"])
    assert page.status_code == 200
    assert "Iniciar sesión" in page.text
    assert "Acceso al registro de boletas." in page.text
    assert "No autorizado." in page.text
    assert 'data-denied="1"' in page.text
    assert "data-denied" in page.text
    assert '"detail"' not in page.text


def test_extract_primary_email_prefers_verified_primary():
    from app.auth.session import _extract_primary_email

    assert _extract_primary_email(
        {
            "primary_email_address_id": "idn_1",
            "email_addresses": [
                {"id": "idn_2", "email_address": "other@example.com", "verification": {"status": "verified"}},
                {
                    "id": "idn_1",
                    "email_address": "Admin@Example.com",
                    "verification": {"status": "verified"},
                },
            ],
        }
    ) == "Admin@Example.com"
    assert _extract_primary_email(
        {
            "email_addresses": [
                {"id": "idn_1", "email_address": "nope@example.com", "verification": {"status": "unverified"}}
            ]
        }
    ) is None


def test_session_without_email_claim_uses_clerk_profile(tmp_path, monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "admin_emails", "admin@example.com")
    monkeypatch.setattr(
        "app.auth.session.authenticate_request",
        lambda request, options: _signed_in_state(None),
    )
    monkeypatch.setattr(
        "app.auth.session._lookup_clerk_email",
        lambda user_id: "Admin@Example.com",
    )
    with _clerk_client(tmp_path, monkeypatch) as client:
        resp = client.get(
            "/",
            headers=_BROWSER,
            cookies={"__session": "session-jwt"},
            follow_redirects=False,
        )
    assert resp.status_code == 200
    assert "Iniciar sesión" not in resp.text


def test_api_allowlist_stays_json(tmp_path, monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "admin_emails", "admin@example.com")
    monkeypatch.setattr(
        "app.auth.session.authenticate_request",
        lambda request, options: _signed_in_state("other@example.com"),
    )

    def explode(user_id):
        raise AssertionError("token already has an email")

    monkeypatch.setattr("app.auth.session._lookup_clerk_email", explode)
    with _clerk_client(tmp_path, monkeypatch) as client:
        denied = client.post(
            "/api/batches",
            json={"label": "x"},
            cookies={"__session": "session-jwt"},
            follow_redirects=False,
        )
        assert denied.status_code == 403
        assert denied.headers["content-type"].startswith("application/json")
        assert denied.json()["detail"] == "Not authorized"
        assert client.get("/api/health").status_code == 200

