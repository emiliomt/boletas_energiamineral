"""A successful Clerk sign-in must leave /login and establish __session.

Regression for the loop where the browser stayed on the login card:
clerk-js v6 ignored afterSignInUrl, and the server treated a signed-in
client (``__client_uat``) with no app ``__session`` cookie as logged out,
so it sent the user back to /login.
"""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.config as app_config


def _pk(host: str = "example.clerk.accounts.dev") -> str:
    enc = base64.urlsafe_b64encode(host.encode()).decode("ascii").rstrip("=")
    return f"pk_test_{enc}"


def _client(tmp_path, monkeypatch) -> TestClient:
    import app.db as app_db
    from app.main import app

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")
    monkeypatch.setattr(app_config.settings, "clerk_publishable_key", _pk())
    monkeypatch.setattr(app_config.settings, "clerk_secret_key", "sk_test_unit")
    monkeypatch.setattr(app_config.settings, "admin_emails", None)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False))
    return TestClient(app)


def _set_cookies(response) -> list[str]:
    return [value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"]


def test_safe_next_path_rejects_login_and_external_targets():
    from app.auth.clerk_handshake import safe_next_path

    assert safe_next_path("/dashboard") == "/dashboard"
    assert safe_next_path("/") == "/"
    assert safe_next_path("/login") == "/"
    assert safe_next_path("/login?next=/") == "/"
    assert safe_next_path("/logout") == "/"
    assert safe_next_path("//evil.com") == "/"
    assert safe_next_path("https://evil.com") == "/"
    assert safe_next_path("/\\evil") == "/"


def test_login_page_cannot_target_itself_and_uses_current_redirect_props(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        html = client.get("/login?next=/login").text
    assert 'data-next="/"' in html
    assert "forceRedirectUrl" in html
    assert "fallbackRedirectUrl" in html
    assert "signUpFallbackRedirectUrl" in html
    assert "afterSignInUrl" not in html
    assert "boletas-clerk-bounce" in html
    assert "Iniciar sesión" in html
    assert "Acceso al registro de boletas." in html


def test_anonymous_document_still_redirects_to_login(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login?next=/")
    assert "handshake" not in resp.headers["location"]


def test_signed_in_client_without_session_cookie_starts_handshake_not_login(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get(
            "/dashboard",
            headers={"accept": "text/html", "sec-fetch-dest": "document"},
            cookies={"__client_uat": "1700000000"},
            follow_redirects=False,
        )
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("https://example.clerk.accounts.dev/v1/client/handshake?")
    assert "format=nonce" in location
    assert "redirect_url=" in location
    assert "dashboard" in location
    assert "/login" not in location
    assert "sk_test" not in location
    assert resp.headers["cache-control"] == "no-store"
    cookies = _set_cookies(resp)
    assert any("__clerk_redirect_count=1" in item for item in cookies)


def test_handshake_redirect_uses_the_public_forwarded_host(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    with _client(tmp_path, monkeypatch) as client:
        resp = client.get(
            "/dashboard",
            headers={
                "accept": "text/html",
                "sec-fetch-dest": "document",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "boletas.example.com",
            },
            cookies={"__client_uat": "1700000000"},
            follow_redirects=False,
        )
    assert resp.status_code == 307
    redirect_url = parse_qs(urlsplit(resp.headers["location"]).query)["redirect_url"]
    assert redirect_url == ["https://boletas.example.com/dashboard"]


def test_handshake_stops_after_repeated_redirects(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get(
            "/",
            headers={"accept": "text/html"},
            cookies={"__client_uat": "1700000000", "__clerk_redirect_count": "3"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_api_request_does_not_handshake(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        resp = client.post(
            "/api/batches",
            json={"label": "x"},
            headers={"accept": "text/html"},
            cookies={"__client_uat": "1700000000"},
            follow_redirects=False,
        )
    assert resp.status_code == 401


def test_handshake_return_sets_session_and_next_request_is_not_sent_to_login(tmp_path, monkeypatch):
    captured: dict[str, str] = {}

    def fake_fetch(nonce: str) -> list[str]:
        captured["nonce"] = nonce
        return ["__session=session-jwt; Path=/; HttpOnly; SameSite=Lax"]

    def fake_authenticate(request, options):
        cookie = request.headers.get("cookie") or ""

        class State:
            pass

        state = State()
        if "session-jwt" in cookie:
            state.is_signed_in = True
            state.is_authenticated = True
            state.payload = {"sub": "user_123", "email": "admin@example.com"}
            state.reason = None
        else:
            state.is_signed_in = False
            state.is_authenticated = False
            state.payload = {}

            class Reason:
                value = ("session-token-missing", "missing")
                name = "SESSION_TOKEN_MISSING"

            state.reason = Reason()
        return state

    monkeypatch.setattr("app.auth.clerk_handshake.fetch_handshake_directives", fake_fetch)
    monkeypatch.setattr("app.auth.session.authenticate_request", fake_authenticate)

    with _client(tmp_path, monkeypatch) as client:
        returned = client.get(
            "/?__clerk_handshake_nonce=nonce-1",
            headers={"accept": "text/html", "sec-fetch-dest": "document"},
            cookies={"__client_uat": "1700000000"},
            follow_redirects=False,
        )
        assert captured["nonce"] == "nonce-1"
        assert returned.status_code == 307
        assert returned.headers["location"] == "/"
        assert "__clerk_handshake_nonce" not in returned.headers["location"]
        assert any("__session=session-jwt" in item for item in _set_cookies(returned))

        home = client.get(
            "/",
            headers={"accept": "text/html", "sec-fetch-dest": "document"},
            cookies={"__session": "session-jwt", "__client_uat": "1700000000"},
            follow_redirects=False,
        )
    assert home.status_code == 200
    assert "Iniciar sesión" not in home.text


def test_stale_session_cookie_is_refreshed_via_handshake(tmp_path, monkeypatch):
    def fake_authenticate(request, options):
        class Reason:
            value = ("token-expired", "expired")
            name = "TOKEN_EXPIRED"

        class State:
            is_signed_in = False
            is_authenticated = False
            payload = {}
            reason = Reason()

        return State()

    monkeypatch.setattr("app.auth.session.authenticate_request", fake_authenticate)
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get(
            "/",
            headers={"accept": "text/html"},
            cookies={"__session": "expired-jwt", "__client_uat": "1700000000"},
            follow_redirects=False,
        )
    assert resp.status_code == 307
    assert "/v1/client/handshake" in resp.headers["location"]
    assert resp.headers["location"].startswith("https://example.clerk.accounts.dev/")
